# Scientific Verification Inventory — Risk Scoring & Decision Engine Subsystem

**Subsystem:** Risk Scoring Engine, Policy AST Evaluator & Decision Infrastructure  
**Repository:** Privacy-preserving Cross-Bank Fraud Detection using Federated Learning  
**Date:** August 2026  
**Auditor:** Senior Researcher in Financial Fraud Detection, Risk Scoring Systems, and Scientific Software Verification  

---

## Executive Overview

This inventory documents every algorithm, mathematical operation, signal normalization function, weighting scheme, AST evaluation logic, and behavioral guarantee implemented in the **Risk Scoring & Decision Engine** subsystem (`backend/app/application/services/risk_engine.py`, `policy_engine.py`, `value_objects_phase2.py`).

---

## Detailed Component Inventory

### 1. Weighted Convex Signal Combiner
* **Component:** `RiskScoringEngine._combine_signals` & `RiskWeightConfig`
* **Purpose:** Combines 9 independent normalized risk signals $s_1, \dots, s_9 \in [0, 1]$ into a unified composite risk ratio $\bar{S} \in [0, 1]$.
* **Mathematical Formulation:**
  $$\bar{S} = \min\left(1.0, \frac{\sum_{k=1}^9 w_k \cdot s_k}{\sum_{k=1}^9 w_k}\right) \quad \text{where } w_k \ge 0$$
* **Risk Scoring Claim:** Convex weighting preserves score normalization on $[0, 1]$ and enables configurable risk policy trade-offs without overflow.
* **Expected Invariant:**
  1. $0.0 \le \bar{S} \le 1.0$ for all valid $s_k \in [0, 1]$ and $w_k \ge 0$.
  2. Scale Invariance: $\bar{S}(c \cdot W) \equiv \bar{S}(W)$ for any scalar multiplier $c > 0$.
  3. Zero Weights Fallback: $\bar{S} = 0.0$ when $\sum w_k = 0$.
* **Possible Implementation Risks:** Division by zero if all weights are zero ($\sum w_k = 0$); unconstrained negative weights causing un-bounded negative risk scores.
* **Edge Cases:** All $w_k = 0$; all $s_k = 1.0$; floating-point precision loss when weights differ by orders of magnitude.
* **Scientific Claim Being Made:** The weighted combination is a convex linear operation over bounded normalized signals, guaranteeing monotonic score composition.
* **Appropriate Verification Methodology:** Property-based testing (Hypothesis scale invariance $W \to cW$), numerical reference verification ($\text{MAE} = 0.00\text{e}+00$).

---

### 2. ML Prediction Confidence Evaluator
* **Component:** `RiskScoringEngine._eval_ml_prediction`
* **Purpose:** Transforms primary ML model fraud probability prediction $p_{\text{ml}}$ into a normalized risk signal $s_{\text{ml}}$.
* **Mathematical Formulation:**
  $$s_{\text{ml}} = \max\left(0.0, \min(1.0, p_{\text{ml}})\right)$$
* **Risk Scoring Claim:** High ML confidence probability directly elevates composite transaction risk.
* **Expected Invariant:**
  1. $0.0 \le s_{\text{ml}} \le 1.0$.
  2. Monotonicity: $p_1 \ge p_2 \implies s_{\text{ml}}(p_1) \ge s_{\text{ml}}(p_2)$.
* **Possible Implementation Risks:** Negative probabilities ($p_{\text{ml}} < 0$) or probabilities exceeding 1.0 ($p_{\text{ml}} > 1$) driving un-clamped signal values.
* **Edge Cases:** $p_{\text{ml}} = 0.0$, $p_{\text{ml}} = 1.0$, $p_{\text{ml}} = -1.0$, $p_{\text{ml}} = \text{NaN}$, $p_{\text{ml}} = +\infty$.
* **Scientific Claim Being Made:** Primary ML inference output acts as a monotonic risk factor with $25\%$ default weight in the hybrid decision framework.
* **Appropriate Verification Methodology:** Property-based testing (Monotonicity), Unit tests for negative/NaN clamping.

---

### 3. Velocity Linear Ramp Evaluator
* **Component:** `RiskScoringEngine._eval_velocity`
* **Purpose:** Converts rolling 1-hour transaction velocity $v$ (txns/hr) into a normalized risk score $s_{\text{vel}}$.
* **Mathematical Formulation:**
  $$s_{\text{vel}} = \min\left(1.0, \max\left(0.0, \frac{v - 2}{8}\right)\right) = \begin{cases} 0.0 & \text{if } v \le 2 \\ \frac{v - 2}{8} & \text{if } 2 < v < 10 \\ 1.0 & \text{if } v \ge 10 \end{cases}$$
* **Risk Scoring Claim:** Transaction rates $\le 2$ txns/hr carry zero velocity risk, while rates $\ge 10$ txns/hr saturate to maximum velocity risk ($1.0$).
* **Expected Invariant:** Continuous piecewise linear ramp on $[2, 10]$ with $s_{\text{vel}} \in [0.0, 1.0]$.
* **Possible Implementation Risks:** Unbounded velocity $v \to \infty$ or negative velocity $v < 0$.
* **Edge Cases:** $v = 0$, $v = 2$, $v = 10$, $v = 1000$, $v = -5$.
* **Scientific Claim Being Made:** Transaction velocity exhibits threshold saturation, matching empirical high-frequency card testing fraud patterns.
* **Appropriate Verification Methodology:** Numerical boundary testing ($v \in [0, 2, 6, 10, 15]$), unit test assertion.

---

### 4. Merchant Reputation & Category Risk Evaluator
* **Component:** `RiskScoringEngine._eval_merchant_reputation` & `MERCHANT_RISK`
* **Purpose:** Blends specific merchant risk score $m_{\text{score}}$ with merchant Category Code (MCC) risk $c_{\text{risk}}$.
* **Mathematical Formulation:**
  $$s_{\text{merch}} = \max\left(0.0, \min\left(1.0, 0.6 \cdot m_{\text{score}} + 0.4 \cdot c_{\text{risk}}\right)\right)$$
  $$\text{where } c_{\text{risk}} = \text{MERCHANT\_RISK.get}(\text{category}, 0.10)$$
* **Risk Scoring Claim:** Blends merchant-level historical chargeback risk ($60\%$) with domain MCC risk ($40\%$).
* **Expected Invariant:** $0.0 \le s_{\text{merch}} \le 1.0$; unrated merchants default to $0.10$ category risk.
* **Possible Implementation Risks:** Missing merchant category defaulting to $0.0$ or un-clamped negative merchant risk scores.
* **Edge Cases:** Category `"gambling"` ($0.90$), `"crypto"` ($0.85$), unknown category `"weapons_smuggling"`, $m_{\text{score}} = -1.0$.
* **Scientific Claim Being Made:** Category risk provides an empirical prior when individual merchant historical transaction volume is low.
* **Appropriate Verification Methodology:** Reference implementation verification, robustness test against unknown categories.

---

### 5. FATF Country Jurisdictional Risk Evaluator
* **Component:** `RiskScoringEngine._eval_country_risk` & `COUNTRY_RISK`
* **Purpose:** Maps ISO 3166-1 alpha-2 country codes to FATF-aligned AML jurisdictional risk scores $s_{\text{country}}$.
* **Mathematical Formulation:**
  $$s_{\text{country}} = \text{COUNTRY\_RISK.get}(\text{str}(\text{code}).\text{upper}(), 0.15)$$
* **Risk Scoring Claim:** FATF blacklisted jurisdictions (North Korea $1.00$, Iran $0.95$) and grey-listed jurisdictions (Nigeria $0.85$, Russia $0.80$) assign high sanctions risk.
* **Expected Invariant:** Case-insensitive lookup; non-sanctioned unknown countries default to low-risk $0.15$.
* **Possible Implementation Risks:** Case-sensitivity bug allowing lowercase `"kp"` to evade sanctions lookup and default to $0.15$.
* **Edge Cases:** `"KP"`, `"kp"`, `"Kp"`, `"US"`, `"ZZ"` (unknown), `None`.
* **Scientific Claim Being Made:** Jurisdictional risk alignment enforces international FATF Recommendation 16 sanctions compliance.
* **Appropriate Verification Methodology:** Robustness test for uppercase/lowercase `"kp"`, dictionary completeness check.

---

### 6. Device Channel Anomaly Evaluator
* **Component:** `RiskScoringEngine._eval_device_anomaly`
* **Purpose:** Maps transaction channel/device type to static discrete risk scores $s_{\text{device}}$.
* **Mathematical Formulation:**
  $$s_{\text{device}} = \text{DEVICE\_SCORES.get}(\text{device}, 0.20)$$
  $$\text{where } \text{DEVICE\_SCORES} = \{\text{pos}: 0.05, \text{mobile}: 0.10, \text{web}: 0.15, \text{atm}: 0.35, \text{phone}: 0.40\}$$
* **Risk Scoring Claim:** Unattended or phone-banking channels carry higher inherent fraud vulnerability than chip-and-pin POS terminals.
* **Expected Invariant:** Fully deterministic discrete mapping; unknown devices default to $0.20$.
* **Possible Implementation Risks:** Unhandled new device channels.
* **Edge Cases:** `"phone_banking"`, `"pos_terminal"`, `"quantum_neural_implant"` (unknown).
* **Scientific Claim Being Made:** Channel fraud rates follow empirical Card-Not-Present (CNP) vs Card-Present (CP) risk distributions.
* **Appropriate Verification Methodology:** Discrete table verification across all 5 standard device types and unknown fallback.

---

### 7. Customer History & Account Tenure Penalty Evaluator
* **Component:** `RiskScoringEngine._eval_customer_history`
* **Purpose:** Evaluates customer historical trust score $h \in [0, 1]$ and applies a new account tenure penalty ($< 30$ days).
* **Mathematical Formulation:**
  $$s_{\text{hist}} = \min\left(1.0, \max\left(0.0, (1.0 - \min(1.0, h)) + \begin{cases} 0.30 & \text{if } \text{age\_days} < 30 \\ 0.0 & \text{otherwise} \end{cases}\right)\right)$$
* **Risk Scoring Claim:** Low trust score $h \to 0$ and account age $< 30$ days independently elevate risk.
* **Expected Invariant:** Discontinuity cliff of $-0.30$ risk when account age transitions from Day 29 to Day 30; $s_{\text{hist}} \le 1.0$.
* **Possible Implementation Risks:** Negative trust scores $h < 0$ driving un-clamped signal values $> 1.0$.
* **Edge Cases:** $h = 1.0, \text{age} = 10$; $h = 0.0, \text{age} = 365$; $\text{age} = 29$ vs $\text{age} = 30$.
* **Scientific Claim Being Made:** Account age $< 30$ days acts as a high-risk indicator for synthetic identity creation.
* **Appropriate Verification Methodology:** Sensitivity analysis of age boundary ($29 \to 30$), unit tests for negative history clamping.

---

### 8. Previous Alerts Tracking Evaluator
* **Component:** `RiskScoringEngine._eval_previous_alerts`
* **Purpose:** Evaluates historical entity alert count $cnt$ into a linear risk signal $s_{\text{alerts}}$.
* **Mathematical Formulation:**
  $$s_{\text{alerts}} = \begin{cases} \min\left(1.0, \frac{cnt}{5}\right) & \text{if } cnt > 0 \\ 0.0 & \text{otherwise} \end{cases}$$
* **Risk Scoring Claim:** 0 prior alerts carry 0 risk; 5 or more prior alerts saturate to maximum risk ($1.0$).
* **Expected Invariant:** Linear ramp on $cnt \in [0, 5]$ with $s_{\text{alerts}} \in [0.0, 1.0]$.
* **Possible Implementation Risks:** In-memory alert state reset on application restart.
* **Edge Cases:** $cnt = 0$, $cnt = 1$, $cnt = 5$, $cnt = 100$.
* **Scientific Claim Being Made:** Recurrent suspicious activity triggers linear escalation in overall entity risk profile.
* **Appropriate Verification Methodology:** State persistence verification, unit testing across alert counts $0 \dots 10$.

---

### 9. Chargeback History Rate Evaluator
* **Component:** `RiskScoringEngine._eval_chargeback_history`
* **Purpose:** Converts historical entity chargeback rate $r$ into a normalized risk score $s_{\text{cb}}$.
* **Mathematical Formulation:**
  $$s_{\text{cb}} = \min(1.0, r \cdot 10.0)$$
* **Risk Scoring Claim:** A $10\%$ chargeback rate ($r = 0.10$) saturates to maximum risk ($1.0$).
* **Expected Invariant:** Linear scaling up to $r = 0.10$; $s_{\text{cb}} \in [0.0, 1.0]$.
* **Possible Implementation Risks:** Negative chargeback rates causing negative risk scores.
* **Edge Cases:** $r = 0.0$, $r = 0.01$, $r = 0.10$, $r = 0.50$.
* **Scientific Claim Being Made:** Merchants or entities exceeding Card Scheme chargeback thresholds ($1.0\%$) undergo rapid risk escalation.
* **Appropriate Verification Methodology:** Reference implementation verification, numerical rate scaling assertions.

---

### 10. Behavioral Z-Score Amount Anomaly Evaluator
* **Component:** `RiskScoringEngine._eval_behavior_anomaly`
* **Purpose:** Measures transaction amount deviation from customer historical baseline $(\mu, \sigma)$.
* **Mathematical Formulation:**
  $$z = \begin{cases} \frac{|\text{amt} - \mu|}{\sigma} & \text{if } \sigma > 0 \\ 10.0 & \text{if } \sigma = 0 \text{ and } |\text{amt} - \mu| > 10^{-6} \\ 0.0 & \text{if } \sigma = 0 \text{ and } |\text{amt} - \mu| \le 10^{-6} \end{cases}$$
  $$s_{\text{behavior}} = \min\left(1.0, \max\left(0.0, \frac{z - 1}{3}\right)\right)$$
* **Risk Scoring Claim:** Deviations within $1\sigma$ carry zero anomaly risk ($0.0$), while deviations $\ge 4\sigma$ saturate to maximum risk ($1.0$). Zero-variance baselines ($\sigma = 0$) flag any non-zero amount difference.
* **Expected Invariant:** $s_{\text{behavior}} \in [0.0, 1.0]$; handles zero-variance baselines without division by zero.
* **Possible Implementation Risks:** Division by zero if $\sigma = 0$; zero-variance blindness returning $z = 0.0$ when $\sigma = 0$.
* **Edge Cases:** $\sigma = 0, \text{amt} = 100, \mu = 100$; $\sigma = 0, \text{amt} = 1000, \mu = 100$; $\sigma = 50, \text{amt} = 250, \mu = 100$ ($z=3\sigma \implies s=0.67$).
* **Scientific Claim Being Made:** Financial transaction amounts follow log-normal or Gaussian local baselines where statistical z-score deviations detect account takeovers (ATO).
* **Appropriate Verification Methodology:** Reference verification against z-score formulas, unit testing of zero-variance baselines.

---

### 11. Score Scaling & Disjoint Risk Tier Mapper
* **Component:** `RiskScore.score` & `RiskScore.risk_level`
* **Purpose:** Scales composite risk ratio $\bar{S} \in [0, 1]$ to integer score $[0, 1000]$ and maps to 5 qualitative risk tiers.
* **Mathematical Formulation:**
  $$\text{Score} = \text{round}(\bar{S} \cdot 1000.0, 1)$$
  $$\text{RiskLevel}(S) = \begin{cases} \text{critical} & \text{if } S \ge 800.0 \\ \text{high} & \text{if } 600.0 \le S < 800.0 \\ \text{medium} & \text{if } 400.0 \le S < 600.0 \\ \text{low} & \text{if } 200.0 \le S < 400.0 \\ \text{minimal} & \text{if } S < 200.0 \end{cases}$$
* **Risk Scoring Claim:** Every composite score maps uniquely to exactly one disjoint qualitative tier.
* **Expected Invariant:** Partition completeness over $[0, 1000]$; strict monotonic tier progression.
* **Possible Implementation Risks:** Floating-point boundary rounding errors (e.g. $799.999 \to 800.0$).
* **Edge Cases:** $S = 0.0, 199.9, 200.0, 399.9, 400.0, 599.9, 600.0, 799.9, 800.0, 1000.0$.
* **Scientific Claim Being Made:** Qualitative risk tier partitioning provides interpretable decision thresholds for automated action routing (ALLOW, REVIEW, BLOCK).
* **Appropriate Verification Methodology:** Disjoint boundary testing across all 15 tier edge cases.

---

### 12. Declarative AST Policy Screening Engine
* **Component:** `policy_engine.evaluate_condition` & `PolicyEngineService`
* **Purpose:** Recursively evaluates declarative JSON AST business rules against transaction & risk score contexts.
* **Mathematical Formulation:**
  Recursive boolean evaluation over AST syntax tree:
  $$\text{Eval}(\text{AST}, C) = \begin{cases} \bigwedge_{c \in \text{AST.and}} \text{Eval}(c, C) & \text{if } \text{and} \in \text{AST} \\ \bigvee_{c \in \text{AST.or}} \text{Eval}(c, C) & \text{if } \text{or} \in \text{AST} \\ \neg \text{Eval}(c, C) & \text{if } \text{not} \in \text{AST} \\ C[\text{field}] \otimes \text{value} & \text{if leaf node } (\otimes \in \{==, !=, >, \ge, <, \le, \text{in}, \text{not in}\}) \end{cases}$$
* **Risk Scoring Claim:** Enables real-time hot-reloading of complex boolean fraud policies without code deployments.
* **Expected Invariant:** Exception-safe fallback (returns `False` on malformed rules or missing fields); case-insensitive string comparison.
* **Possible Implementation Risks:** Infinite recursion on cyclic ASTs; type comparison mismatches (e.g. string to float).
* **Edge Cases:** Deeply nested ASTs, missing fields in context, string vs numeric comparisons, invalid operator types.
* **Scientific Claim Being Made:** AST rule evaluation provides a formal language for expert domain knowledge injection into automated decision pipelines.
* **Appropriate Verification Methodology:** Robustness testing with malformed ASTs, property-based testing over random AST payloads.

---

*This inventory establishes the baseline for the scientific verification program for the Risk Scoring & Decision Engine.*
