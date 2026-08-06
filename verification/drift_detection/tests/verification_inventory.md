# Concept Drift Detection & Calibration Subsystem: Complete Scientific Verification Inventory

**Target Subsystem:** Model Drift Detection, Statistical Distribution Analytics, Model Calibration, and Automated Retraining Triggers  
**Audited Source Files:**
- `app/application/services/drift_service.py` (`ModelDriftService`, `FeatureDriftMetrics`, `CalibrationReport`, `DriftAnalysisReport`)
- `app/application/services/retraining_trigger_engine.py` (`RetrainingTriggerEngine`)
- `app/application/services/automated_retraining.py` (`DriftTriggeredRetrainingService`)
- `app/application/services/auto_rollback.py` (`AutoRollbackManager`)
- `app/presentation/routers/monitoring.py` (Monitoring API endpoints)

**Auditor Role:** Senior Researcher in Concept Drift Detection, Statistical Machine Learning, Data Distribution Analysis, and Scientific Software Verification  
**Date:** July 31, 2026  

---

## Executive Overview of the Inventory

This document provides an exhaustive, element-by-element scientific audit and verification inventory of the Model Drift Detection and Calibration Analytics engine. Each statistical method, numerical aggregation, thresholding rule, and automated lifecycle trigger is cataloged with its exact mathematical formulation, underlying statistical assumptions, code-level invariants, potential implementation risks, edge cases, explicit scientific claims, and recommended verification methodologies.

---

## Inventory of Implemented Components

---

### Component 1: Population Stability Index (PSI) Calculation Engine

- **Component:** `ModelDriftService._calculate_psi(actual, expected, num_bins=10)`
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 80–107
- **Purpose:** Quantifies the shift between a reference (expected) feature or prediction distribution $P$ and a live monitoring (actual) distribution $Q$ using discretized relative entropy.
- **Mathematical Formulation:**
  For reference sample $X_{\text{ref}} \sim P$ and current sample $X_{\text{curr}} \sim Q$, quantile bin edges $\{b_0, b_1, \dots, b_k\}$ are derived from $X_{\text{ref}}$ such that $P(b_{i-1} \le X_{\text{ref}} < b_i) \approx 1/k$.
  
  Relative frequencies are smoothed with Laplace/additive parameter $\epsilon = 10^{-4}$:
  $$\tilde{p}_i = \frac{c_{P, i} + \epsilon}{|X_{\text{ref}}| + k \epsilon}, \quad \tilde{q}_i = \frac{c_{Q, i} + \epsilon}{|X_{\text{curr}}| + k \epsilon}$$
  
  The Population Stability Index is computed as symmetric Kullback-Leibler (KL) divergence:
  $$\text{PSI}(Q \parallel P) = \sum_{i=1}^{k} \left( \tilde{q}_i - \tilde{p}_i \right) \cdot \ln\left( \frac{\tilde{q}_i}{\tilde{p}_i} \right)$$
  
  Non-negativity is enforced:
  $$\text{PSI}_{\text{final}} = \max\left(0.0, \text{PSI}(Q \parallel P)\right)$$

- **Statistical Claim:** 
  - $\text{PSI} < 0.10 \implies$ No significant distribution shift (STABLE).
  - $0.10 \le \text{PSI} < 0.20 \implies$ Moderate distribution shift (MODERATE_DRIFT).
  - $\text{PSI} \ge 0.20 \implies$ Severe distribution shift requiring model retraining (CRITICAL).

- **Expected Invariants:**
  1. **Non-negativity:** $\text{PSI}(Q \parallel P) \ge 0$ for all valid probability distributions $P, Q$.
  2. **Identity Invariance:** $\text{PSI}(P \parallel P) = 0$ (up to Laplace smoothing artifact $\approx O(\epsilon)$).
  3. **Scale Invariance:** Multiplying inputs by a positive scalar constant $c > 0$ preserves quantile bin ordering and results in identical PSI.
  4. **Sample Length Independence:** PSI value is asymptotically independent of $|X_{\text{curr}}|$ and $|X_{\text{ref}}|$ for i.i.d. draws from fixed $P$ and $Q$.

- **Possible Implementation Risks:**
  - **Quantile Collapse for Constant/Discrete Data:** If $X_{\text{ref}}$ contains duplicate values (e.g., zero-inflated features like alert counts), `np.percentile` produces duplicate bin edges, causing `np.unique` to return fewer than $k$ bins. The fallback uses `np.linspace(min, max, num_bins+1)`, which can create unequal bin counts and empty bins.
  - **Laplace Smoothing Bias:** Fixed additive constant $\epsilon = 10^{-4}$ distorts true relative entropy when bin counts are small ($c_i \approx 0$), overestimating PSI for sparse bins.

- **Edge Cases:**
  - Empty inputs ($|X_{\text{curr}}| = 0$ or $|X_{\text{ref}}| = 0$) $\implies$ returns $0.0$.
  - Constant inputs ($X_{\text{curr}} = [c, c, \dots, c]$, $X_{\text{ref}} = [c, c, \dots, c]$) $\implies$ single unique bin fallback; returns $0.0$.
  - Completely non-overlapping support ($\text{supp}(P) \cap \text{supp}(Q) = \emptyset$) $\implies$ smoothed ratios dominate, capping PSI at finite value rather than $\infty$.

- **Scientific Claim Being Made:**
  The symmetric Jeffrey's divergence on discretized quantiles reliably detects arbitrary univariate continuous distribution drift without parametric distributional assumptions.

- **Appropriate Verification Methodology:**
  - *Independent Mathematical Reference:* Compare `_calculate_psi` against `scipy.stats.entropy(q, p) + scipy.stats.entropy(p, q)`.
  - *Property-Based Testing (Hypothesis):* Verify $\text{PSI}(X, X) \approx 0$, $\text{PSI}(X, Y) \ge 0$, and scale invariance $\text{PSI}(aX+b, aY+b) = \text{PSI}(X, Y)$.
  - *Monte Carlo Simulation:* Draw $N$ samples from $\mathcal{N}(0, 1)$ vs $\mathcal{N}(\mu, 1)$ for $\mu \in [0, 2]$; plot theoretical vs computed PSI curve.

---

### Component 2: Two-Sample Kolmogorov-Smirnov (KS) Test Integrator

- **Component:** `ModelDriftService.analyze_feature_drift` (KS Test Section)
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 126–130
- **Purpose:** Performs a non-parametric hypothesis test of the null hypothesis $H_0: P_{\text{curr}} = P_{\text{ref}}$ against $H_1: P_{\text{curr}} \neq P_{\text{ref}}$.
- **Mathematical Formulation:**
  Let $F_{\text{curr}}(x)$ and $F_{\text{ref}}(x)$ be the empirical cumulative distribution functions (eCDFs):
  $$F_n(x) = \frac{1}{n} \sum_{i=1}^{n} \mathbb{I}(X_i \le x)$$
  
  The two-sample KS statistic $D_{n, m}$ is the supremum distance between eCDFs:
  $$D_{n, m} = \sup_{x} \left| F_{\text{curr}, n}(x) - F_{\text{ref}, m}(x) \right|$$
  
  The p-value is computed from the asymptotic Kolmogorov distribution:
  $$P\left(K \ge D_{n, m} \sqrt{\frac{n m}{n + m}}\right) \approx 2 \sum_{k=1}^{\infty} (-1)^{k-1} \exp\left(-2 k^2 z^2\right)$$

- **Statistical Claim:**
  - $p < 0.01 \implies$ Reject $H_0$ with 99% confidence (classified as SEVERE_DRIFT if combined with high PSI).
  - $p < 0.05 \implies$ Reject $H_0$ with 95% confidence (classified as MODERATE_DRIFT).

- **Expected Invariants:**
  1. **Bounded Statistic:** $D_{n, m} \in [0, 1]$.
  2. **Bounded p-value:** $p \in [0, 1]$.
  3. **Identical Samples:** For $X_{\text{curr}} = X_{\text{ref}}$, $D_{n, m} = 0.0$ and $p = 1.0$.
  4. **Monotonicity with Sample Size:** For a fixed distribution shift $\Delta \mu > 0$, as $n, m \to \infty$, $p \to 0$ monotonically.

- **Possible Implementation Risks:**
  - **Over-Sensitivity to Sample Size:** The KS test is notorious for yielding $p < 0.001$ for trivial, clinically meaningless shifts when sample sizes are large ($n, m > 10,000$).
  - **Discrete Data Violation:** The KS test assumes continuous distributions. For discrete features (e.g., alert counts, risk levels), tie-handling in `scipy.stats.ks_2samp` is conservative and alters exact p-values.

- **Edge Cases:**
  - Unimodal vs Bimodal distributions with identical mean and variance $\implies$ KS statistic captures maximum eCDF separation.
  - Zero-variance inputs ($X_{\text{curr}} = [1, 1, 1]$, $X_{\text{ref}} = [1, 1, 1]$) $\implies$ $D=0, p=1$.

- **Scientific Claim Being Made:**
  The non-parametric two-sample Kolmogorov-Smirnov test reliably identifies shifts in empirical cumulative distribution functions independent of feature scaling or functional form.

- **Appropriate Verification Methodology:**
  - *Reference Verification:* Compare `stats.ks_2samp` outputs against independent eCDF supremum computation.
  - *Statistical Calibration Test:* Under $H_0$ (drawing both samples from $\mathcal{N}(0,1)$ across 1,000 trials), verify that the empirical p-value distribution is Uniform(0, 1) and Type I error rate matches $\alpha = 0.05$.

---

### Component 3: Wasserstein Distance (Earth Mover's Distance) Calculator

- **Component:** `ModelDriftService.analyze_feature_drift` (Wasserstein Section)
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 131–132
- **Purpose:** Measures the minimum work required to transform the current feature distribution into the reference feature distribution.
- **Mathematical Formulation:**
  For 1D continuous random variables with cumulative distribution functions $U(x)$ and $V(x)$, the 1st Wasserstein distance ($W_1$) simplifies to the integral of the absolute difference between eCDFs:
  $$W_1(u, v) = \int_{-\infty}^{\infty} |U(x) - V(x)| \, dx$$
  
  Equivalently, using inverse CDFs (quantile functions $U^{-1}, V^{-1}$):
  $$W_1(u, v) = \int_{0}^{1} \left| U^{-1}(q) - V^{-1}(q) \right| \, dq$$

- **Statistical Claim:**
  Provides a scale-dependent, metric-space distance measure of distribution shift that is robust to non-overlapping supports (unlike KL divergence) and reflects absolute shift magnitude in feature units.

- **Expected Invariants:**
  1. **Metric Axioms:**
     - Non-negativity: $W_1(u, v) \ge 0$.
     - Identity of Indiscernibles: $W_1(u, v) = 0 \iff u = v$.
     - Symmetry: $W_1(u, v) = W_1(v, u)$.
     - Triangle Inequality: $W_1(u, w) \le W_1(u, v) + W_1(v, w)$.
  2. **Translation Equivariance:** $W_1(u + c, v + c) = W_1(u, v)$ for constant $c$.
  3. **Scale Equivariance:** $W_1(a u, a v) = |a| \cdot W_1(u, v)$ for scalar multiplier $a$.

- **Possible Implementation Risks:**
  - **Lack of Normalized Scale:** Unlike PSI ($\in [0, \infty)$ with standardized thresholds $0.10, 0.20$) or KS p-value ($\in [0, 1]$), Wasserstein distance is expressed in native feature units (e.g., transaction amount in USD vs account age in days). Comparing raw $W_1$ values across different features without normalization is scientifically invalid.

- **Edge Cases:**
  - Uniform shift: $X_{\text{curr}} = X_{\text{ref}} + c \implies W_1 = |c|$.
  - Single point distributions: $\delta(a)$ vs $\delta(b) \implies W_1 = |a - b|$.

- **Scientific Claim Being Made:**
  $W_1$ distance accurately measures geometric distribution shift in metric space without experiencing gradient vanishing or numerical instability on non-overlapping probability supports.

- **Appropriate Verification Methodology:**
  - *Reference Verification:* Compare `stats.wasserstein_distance` against trapezoidal integration of $|F_1(x) - F_2(x)|$.
  - *Property Testing:* Verify metric axioms (symmetry, triangle inequality, scale equivariance) using Hypothesis on randomized vectors.

---

### Component 4: Model Calibration — Brier Score Evaluator

- **Component:** `ModelDriftService.compute_calibration` (Brier Score Section)
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 177–178, 216
- **Purpose:** Measures the mean squared difference between predicted fraud probabilities and binary ground-truth outcomes.
- **Mathematical Formulation:**
  Given binary labels $y_i \in \{0, 1\}$ and predicted probabilities $\hat{p}_i \in [0, 1]$ for $N$ samples:
  $$\text{BS} = \frac{1}{N} \sum_{i=1}^{N} \left( \hat{p}_i - y_i \right)^2$$
  
  Brier score decomposition (Murphy, 1973):
  $$\text{BS} = \text{Reliability} - \text{Resolution} + \text{Uncertainty}$$

- **Statistical Claim:**
  - $\text{BS} \le 0.15 \implies$ Model is well-calibrated (when combined with $\text{ECE} \le 0.10$).
  - Lower Brier score indicates superior probability calibration and discriminative accuracy.

- **Expected Invariants:**
  1. **Bounded Domain:** $\text{BS} \in [0.0, 1.0]$ for binary classification.
  2. **Perfect Predictions:** If $\hat{p}_i = y_i$ for all $i$, $\text{BS} = 0.0$.
  3. **Worst Predictions:** If $\hat{p}_i = 1 - y_i$ for all $i$, $\text{BS} = 1.0$.
  4. **Uninformative Random Guessing:** For balanced data ($P(y=1) = 0.5$) and uniform prediction $\hat{p}_i = 0.5$, $\text{BS} = 0.25$.

- **Possible Implementation Risks:**
  - **Base-Rate Sensitivity:** Brier score is heavily influenced by class prevalence $p = P(y=1)$. For highly imbalanced fraud data ($p \approx 0.01$), a trivial zero-classifier ($\hat{p}_i = 0$) achieves $\text{BS} = 0.01$, falsely signaling an "excellent" model despite zero fraud recall.

- **Edge Cases:**
  - Single sample ($N=1$) $\implies \text{BS} = (\hat{p}_1 - y_1)^2$.
  - All fraud ($y = [1, 1, \dots, 1]$) with $\hat{p} = [0.9, 0.9, \dots] \implies \text{BS} = 0.01$.

- **Scientific Claim Being Made:**
  Brier score is a strictly proper scoring rule: its expected value is uniquely minimized when predicted probabilities equal true conditional probabilities $\hat{p}_i = P(y_i=1 \mid x_i)$.

- **Appropriate Verification Methodology:**
  - *Reference Verification:* Compare `compute_calibration` Brier score against `sklearn.metrics.brier_score_loss`.
  - *Strict Property Verification:* Verify proper scoring property: show that predicting true base rate $\bar{y}$ yields lower Brier score than any biased prediction $\bar{y} + \delta$.

---

### Component 5: Expected Calibration Error (ECE) & Maximum Calibration Error (MCE)

- **Component:** `ModelDriftService.compute_calibration` (ECE & MCE Section)
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 180–225
- **Purpose:** Measures the alignment between predicted confidence scores and empirical accuracy across probability bins.
- **Mathematical Formulation:**
  Partition probability domain $[0, 1]$ into $M$ equal-width bins $B_1, B_2, \dots, B_M$ where $B_m = \left[\frac{m-1}{M}, \frac{m}{M}\right)$.
  
  For each bin $B_m$ containing $N_m = |\{i : \hat{p}_i \in B_m\}|$ samples:
  - Mean predicted probability: $\text{acc}(B_m) = \frac{1}{N_m} \sum_{i \in B_m} \hat{p}_i$
  - Empirical fraud ratio: $\text{conf}(B_m) = \frac{1}{N_m} \sum_{i \in B_m} y_i$
  - Absolute bin calibration gap: $\text{gap}(B_m) = \left| \text{acc}(B_m) - \text{conf}(B_m) \right|$
  
  **Expected Calibration Error (ECE):**
  $$\text{ECE} = \sum_{m=1}^{M} \frac{N_m}{N} \left| \text{acc}(B_m) - \text{conf}(B_m) \right|$$
  
  **Maximum Calibration Error (MCE):**
  $$\text{MCE} = \max_{m \in \{1, \dots, M\}, N_m > 0} \left| \text{acc}(B_m) - \text{conf}(B_m) \right|$$

- **Statistical Claim:**
  - $\text{ECE} \le 0.10 \land \text{BS} \le 0.15 \implies$ `is_well_calibrated = True`.
  - ECE measures the expected discrepancy between confidence and actual accuracy under equal-width partitioning.

- **Expected Invariants:**
  1. **Bounds:** $0.0 \le \text{ECE} \le \text{MCE} \le 1.0$.
  2. **Perfect Calibration:** If $\hat{p}_i = y_i \in \{0, 1\}$ for all $i$, $\text{ECE} = 0.0$ and $\text{MCE} = 0.0$.
  3. **Bin Sum Invariant:** $\sum_{m=1}^{M} N_m = N$.

- **Possible Implementation Risks:**
  - **Equal-Width Binning Vulnerability:** Equal-width binning can lead to empty or extremely low-count bins when probabilities are skewed (e.g. most fraud scores concentrated in $[0.0, 0.05]$). Empty bins contribute $0$ to ECE, masking miscalibration in unpopulated intervals.
  - **Boundary Inclusion Logic:** Lines 190–192 use `(y_prob >= p_min) & (y_prob <= p_max)` for the final bin ($i = M-1$) vs `< p_max` for earlier bins. While standard, double-counting can occur if float comparisons hit exact boundaries without strict partitioning.

- **Edge Cases:**
  - Empty input lists ($N=0$) $\implies$ returns fallback report (`ECE=0.0`, `is_well_calibrated=True`).
  - Single bin ($M=1$) $\implies \text{ECE} = |\text{mean}(\hat{p}) - \text{mean}(y)|$.

- **Scientific Claim Being Made:**
  ECE with equal-width binning provides a consistent estimator of model probability calibration discrepancy under the $L_1$ norm.

- **Appropriate Verification Methodology:**
  - *Reference Verification:* Compare `compute_calibration` against `netcal.metrics.ECE(bins=10)`.
  - *Synthetic Verification:* Generate perfectly calibrated probabilities $p_i \sim U(0, 1)$ and labels $y_i \sim \text{Bernoulli}(p_i)$; verify $\text{ECE} \to 0$ as $N \to \infty$.

---

### Component 6: Multimodal Drift Analysis & Risk Scoring Classifier

- **Component:** `ModelDriftService.run_full_drift_analysis`
- **Target File & Lines:** `app/application/services/drift_service.py`, Lines 227–275
- **Purpose:** Aggregates feature-level drift (KS test, Wasserstein, PSI), concept-level drift (PSI on risk scores), and calibration to assign a global health status (`HEALTHY`, `WARNING`, `CRITICAL`).
- **Mathematical Formulation:**
  Given feature PSI values $\{\text{PSI}_{f_1}, \dots, \text{PSI}_{f_d}\}$ and concept PSI $\text{PSI}_{\text{concept}}$:
  $$\text{PSI}_{\text{max}} = \max\left( \max_{j} \text{PSI}_{f_j}, \text{PSI}_{\text{concept}} \right)$$
  $$\bar{p}_{\text{KS}} = \frac{1}{d} \sum_{j=1}^{d} p_{\text{KS}, f_j}$$
  
  Decision Tree for System Status:
  $$\text{Status} = \begin{cases}
  \text{CRITICAL}, & \text{if } \text{PSI}_{\text{max}} \ge 0.20 \lor \text{PSI}_{\text{concept}} \ge 0.20 \\
  \text{WARNING}, & \text{else if } \text{PSI}_{\text{max}} \ge 0.10 \lor \text{PSI}_{\text{concept}} \ge 0.10 \\
  \text{HEALTHY}, & \text{otherwise}
  \end{cases}$$
  
  $$\text{auto\_retrain\_triggered} = (\text{Status} == \text{CRITICAL})$$

- **Statistical Claim:**
  Provides a conservative, worst-case bound on system-wide model degradation by triggering critical alerts whenever *any single feature* or *concept prediction score* breaches the critical PSI threshold ($0.20$).

- **Expected Invariants:**
  1. **Monotonic Severity:** If any individual feature's PSI increases above $0.20$, `overall_status` must transition to `CRITICAL`.
  2. **Retrain Alignment:** `auto_retrain_triggered` is `True` if and only if `overall_status` is `CRITICAL`.
  3. **Mean P-value Bound:** $\bar{p}_{\text{KS}} \in [0.0, 1.0]$.

- **Possible Implementation Risks:**
  - **Multiple Hypothesis Testing Vulnerability:** Testing $d$ features simultaneously at significance level $\alpha = 0.05$ without Family-Wise Error Rate (FWER) correction (e.g., Bonferroni or Holm-Bonferroni correction) results in a high probability of false positive drift detection:
    $$P(\ge 1 \text{ false alarm}) = 1 - (1 - \alpha)^d$$
    For $d=12$ features, false alarm rate under $H_0$ is $1 - (0.95)^{12} \approx 46.0\%$.

- **Edge Cases:**
  - Empty feature dicts ($d=0$) $\implies$ falls back to evaluating `concept_psi` on risk scores.
  - All p-values zero $\implies \bar{p}_{\text{KS}} = 0.0$, `status = CRITICAL`.

- **Scientific Claim Being Made:**
  Maximal PSI aggregation across univariate features and prediction scores forms a sufficient statistic for detecting joint covariate shift and concept drift in production ML pipelines.

- **Appropriate Verification Methodology:**
  - *Decision Tree Property Test:* Execute Hypothesis test verifying that whenever `max_psi >= 0.20`, `auto_retrain_triggered` is unconditionally `True`.
  - *False Positive Rate Evaluation:* Simulate 1,000 stationary data windows ($P_{\text{curr}} = P_{\text{ref}}$) and measure empirical false alert rate across feature counts $d \in \{1, 5, 12, 50\}$.

---

### Component 7: Multi-Criteria Retraining Trigger Engine

- **Component:** `RetrainingTriggerEngine.evaluate_triggers`
- **Target File & Lines:** `app/application/services/retraining_trigger_engine.py`, Lines 17–111
- **Purpose:** Evaluates data ingestion volume thresholds, statistical drift metrics (PSI / KS p-value), and cron schedules to dispatch automated model retraining tasks.
- **Mathematical Formulation:**
  Decision rule $\mathcal{D}(N, \text{PSI}, p_{\text{KS}}, \Delta t)$:
  $$T_{\text{ingest}} = \mathbb{I}\left(N \ge N_{\text{thresh}}\right) \quad (N_{\text{thresh}} = 50,000)$$
  $$T_{\text{drift}} = \mathbb{I}\left(\text{PSI} > 0.20 \lor p_{\text{KS}} < 0.05\right)$$
  $$T_{\text{cadence}} = \mathbb{I}\left(\Delta t \ge 24.0 \text{ hours}\right)$$
  
  Retraining Dispatch Condition:
  $$\text{IsTriggered} = T_{\text{ingest}} \lor T_{\text{drift}} \lor T_{\text{cadence}}$$

- **Statistical Claim:**
  Guarantees model freshness and performance stability by retraining whenever (1) sufficient new training data has accumulated, (2) statistically significant drift occurs, or (3) a maximum temporal SLA elapsed.

- **Expected Invariants:**
  1. **Disjunctive Invariant:** If any single condition $T_i = 1$, $\text{IsTriggered}$ must be `True`.
  2. **Quiescence Invariant:** If $N < N_{\text{thresh}}$, $\text{PSI} \le 0.20$, $p_{\text{KS}} \ge 0.05$, and $\Delta t < 24\text{h}$, $\text{IsTriggered}$ must be `False`.
  3. **Reason Auditability:** `trigger_reasons` contains exactly the identifiers corresponding to satisfied conditions.

- **Possible Implementation Risks:**
  - **Timestamp Naivety:** Line 67 computes $\Delta t$ using `datetime.now(UTC)`. If `last_run_timestamp` is passed as a timezone-naive datetime, Python raises a `TypeError` during subtraction.

- **Edge Cases:**
  - `last_run_timestamp = None` $\implies T_{\text{cadence}} = 1$ (triggers immediate initial run).
  - Boundary values ($N = 50,000$, $\text{PSI} = 0.20000001$) $\implies$ strictly evaluates `>=` and `>`.

- **Scientific Claim Being Made:**
  Combining volumetric batch ingestion, non-parametric statistical drift criteria, and periodic temporal cadence prevents catastrophic model decay in non-stationary streaming environments.

- **Appropriate Verification Methodology:**
  - *Combinatorial Property Test:* Test all $2^3 = 8$ boolean trigger state combinations to verify exact mapping to `is_triggered` and `trigger_reasons`.
  - *Timezone Safety Test:* Pass naive, UTC-aware, and non-UTC timezone datetimes to verify robust handling without exceptions.

---

### Component 8: Drift-Triggered Retraining Service & Lifecycle Manager

- **Component:** `DriftTriggeredRetrainingService.evaluate_drift_and_trigger` & `execute_retraining_pipeline`
- **Target File & Lines:** `app/application/services/automated_retraining.py`, Lines 37–104
- **Purpose:** Monitors drift scores and model accuracy (AUC), creates immutable job records, dispatches asynchronous FL retraining, and registers candidate model checkpoints.
- **Mathematical Formulation:**
  Priority evaluation ladder:
  $$\text{Cause} = \begin{cases}
  \text{PSI\_DRIFT\_EXCEEDED}, & \text{if } \text{PSI} \ge 0.20 \\
  \text{CONCEPT\_DRIFT\_DETECTED}, & \text{else if } \text{ConceptScore} \ge 0.15 \\
  \text{ACCURACY\_DEGRADATION}, & \text{else if } \text{AUC} < 0.70 \\
  \text{None}, & \text{otherwise}
  \end{cases}$$

- **Statistical Claim:**
  Automatically repairs model degradation by initiating federated retraining whenever model AUC drops below $0.70$ or population stability index exceeds $0.20$.

- **Expected Invariants:**
  1. **Job Immutability:** Generated `job_id` strings (`retrain_[0-9a-f]{8}`) are globally unique UUID prefixes.
  2. **State Transition Invariant:** Job status transitions strictly from `TRIGGERED` $\to$ `COMPLETED`.
  3. **Candidate Model Versioning:** Every executed job produces a non-null `candidate_model_version` string.

- **Possible Implementation Risks:**
  - **In-Memory Volatility:** `_jobs` dictionary is stored in volatile server memory. A process restart loses retraining history and job tracking context.
  - **Mock Retraining Pipeline:** `execute_retraining_pipeline` returns static dummy metrics (`{"auc": 0.88, "precision": 0.84, "recall": 0.81}`) without actually invoking `fl_engine.py` or training GraphSAGE/MLP models.

- **Edge Cases:**
  - Invalid job ID passed to `execute_retraining_pipeline` $\implies$ raises `KeyError`.
  - Borderline AUC ($0.69999$ vs $0.70001$) $\implies$ correctly triggers at $< 0.70$.

- **Scientific Claim Being Made:**
  Automated retraining workflow maintains bounded statistical performance under continuous data distribution drift.

- **Appropriate Verification Methodology:**
  - *State Machine Testing:* Verify valid state transitions (`TRIGGERED` $\to$ `COMPLETED`) and invalid transitions (e.g. re-executing completed job).
  - *Mock Integration Verification:* Ensure that triggering correctly interacts with `FL_Engine` training workflows.

---

### Component 9: Zero-Downtime Model SLA & Auto-Rollback Manager

- **Component:** `AutoRollbackManager.evaluate_model_health_and_rollback`
- **Target File & Lines:** `app/application/services/auto_rollback.py`, Lines 35–90
- **Purpose:** Monitors real-time model inference metrics (AUC, latency SLA, False Positive Rate) and executes automated zero-downtime rollbacks to a fallback model checkpoint if SLAs are violated.
- **Mathematical Formulation:**
  SLA Health Rules:
  $$C_{\text{AUC}} = \mathbb{I}\left( \text{AUC}_{\text{curr}} < \text{AUC}_{\text{min}} \right) \quad (\text{AUC}_{\text{min}} = 0.65)$$
  $$C_{\text{latency}} = \mathbb{I}\left( \text{Latency}_{\text{curr}} > \text{Latency}_{\text{max}} \right) \quad (\text{Latency}_{\text{max}} = 200.0 \text{ ms})$$
  $$C_{\text{FPR}} = \mathbb{I}\left( \text{FPR}_{\text{curr}} > \text{FPR}_{\text{max}} \right) \quad (\text{FPR}_{\text{max}} = 0.05)$$
  
  Priority Cause Determination:
  $$\text{Cause} = \begin{cases}
  \text{AUC\_DROP\_CRITICAL}, & \text{if } C_{\text{AUC}} = 1 \\
  \text{LATENCY\_SLA\_VIOLATION}, & \text{else if } C_{\text{latency}} = 1 \\
  \text{FPR\_SPIKE}, & \text{else if } C_{\text{FPR}} = 1 \\
  \text{None}, & \text{otherwise}
  \end{cases}$$
  
  If $\text{Cause} \neq \text{None}$:
  $$\text{ActiveModel} \leftarrow \text{FallbackModel}, \quad \text{DemotedModel} \leftarrow \text{ActiveModel}$$

- **Statistical Claim:**
  Guarantees operational reliability and bounded financial risk by instantly demoting faulty or poisoned candidate models if AUC falls below $0.65$, latency exceeds $200\text{ ms}$, or False Positive Rate spikes above $5\%$.

- **Expected Invariants:**
  1. **Rollback Safety Invariant:** If a rollback triggers, `restored_model_version` MUST equal `fallback_model_version` and `demoted_model_version` MUST equal `active_model_version`.
  2. **Audit History Log:** Every executed rollback appends an immutable `RollbackExecutionRecord` to `_history`.
  3. **Priority Ordering:** AUC degradation ($C_{\text{AUC}}$) strictly takes precedence over latency and FPR violations.

- **Possible Implementation Risks:**
  - **In-Memory History:** Like `DriftTriggeredRetrainingService`, `_history` is an in-memory Python list.
  - **No Automated State Synchronization:** The method returns `(True, record)`, but does not directly mutate the active production model state in `model_registry.py`. The calling router must handle the actual pointer swap.

- **Edge Cases:**
  - Equal thresholds ($\text{AUC} = 0.65$, $\text{FPR} = 0.05$) $\implies$ strictly evaluates `<` and `>`, so boundary values do not trigger rollback.
  - Multiple simultaneous violations (AUC=0.60, Latency=300ms, FPR=0.10) $\implies$ correctly assigns highest priority cause `AUC_DROP_CRITICAL`.

- **Scientific Claim Being Made:**
  Continuous SLA health monitoring with automated fallback prevents degraded or poisoned models from serving live production traffic.

- **Appropriate Verification Methodology:**
  - *Priority Order Verification:* Test multi-fault inputs to verify strict priority hierarchy ($\text{AUC} > \text{Latency} > \text{FPR}$).
  - *SLA Boundary Verification:* Verify exact behavior at boundary conditions ($\text{AUC} \in \{0.6499, 0.6500, 0.6501\}$).

---

## Comprehensive Verification Summary

| Component ID | Target Subsystem | Primary Function | Primary Risk / Limitation | Recommended Verification |
|:---:|:---|:---|:---|:---|
| **COMP-1** | `_calculate_psi` | Quantile PSI Calculation | Quantile collapse on zero-inflated data; fixed Laplace smoothing bias | Reference verification vs `scipy.stats.entropy`; Hypothesis property testing |
| **COMP-2** | `stats.ks_2samp` | Kolmogorov-Smirnov 2-Sample Test | Over-sensitivity to large sample sizes ($N > 10,000$); false alarm inflation | Reference eCDF supremum test; uniform p-value calibration under $H_0$ |
| **COMP-3** | `stats.wasserstein_distance` | 1D Earth Mover's Distance | Native feature unit scale dependency (un-normalized cross-feature comparison) | Metric axiom property tests (symmetry, triangle inequality); reference integral test |
| **COMP-4** | `compute_calibration` (Brier) | Brier Score Evaluation | Base-rate sensitivity under extreme fraud class imbalance ($p \approx 0.01$) | Scikit-learn reference comparison; proper scoring rule property proof |
| **COMP-5** | `compute_calibration` (ECE/MCE) | Expected Calibration Error | Equal-width binning failure under skewed probabilities (empty bins) | Netcal library benchmark comparison; perfectly calibrated synthetic data test |
| **COMP-6** | `run_full_drift_analysis` | Multimodal Drift Classifier | Multiple hypothesis testing false alarm inflation ($P \ge 46\%$ for $d=12$) | Decision tree invariant testing; false alert rate simulation over stationary windows |
| **COMP-7** | `RetrainingTriggerEngine` | Multi-Criteria Retrain Trigger | Timezone naivety in datetime subtraction | Combinatorial boolean property testing; timezone safety checks |
| **COMP-8** | `DriftTriggeredRetrainingService` | Async Retraining Job Pipeline | Volatile in-memory state; mock execution metrics in current service | State machine transition verification; mock integration testing |
| **COMP-9** | `AutoRollbackManager` | Zero-Downtime SLA Rollback | In-memory history; lacks direct `model_registry.py` pointer mutation | Priority hierarchy property testing; SLA boundary verification |

---

## Scientific Summary & Recommended Next Steps

1. **Mathematical Soundness:** The mathematical foundations of the drift detection subsystem (KS test, Wasserstein distance, PSI, Brier score, ECE) are theoretical standard choices for distribution drift and model calibration monitoring.
2. **Key Scientific Vulnerabilities Identified:**
   - **Quantile Collapse in PSI:** Zero-inflated features (e.g. alert counts) cause percentile collapse in `_calculate_psi`, falling back to linear binning that distorts PSI scores.
   - **Multiple Testing False Alarm Inflation:** Testing 12+ features simultaneously without Bonferroni or False Discovery Rate (FDR) correction guarantees a $\approx 46\%$ false drift alarm rate under stationary conditions.
   - **Brier Score Base-Rate Bias:** In imbalanced fraud detection, raw Brier score is dominated by negative samples and should be supplemented with Class-Weighted Brier Score or Stratified Calibration Error.
3. **Execution Plan:** To achieve publication-quality verification, we must now build independent mathematical reference benchmarks, Hypothesis property-based test suites, robustness failure injection tests, and statistical Monte Carlo simulations for this subsystem.
