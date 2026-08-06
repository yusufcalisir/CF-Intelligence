# Decision System & Sensitivity Analysis Report — Risk Scoring Engine Subsystem

**Subsystem:** Risk Scoring Engine (`RiskScoringEngine`) & Hybrid Decision Governance  
**Repository:** Privacy-preserving Cross-Bank Fraud Detection using Federated Learning  
**Date:** August 2026  
**Auditor:** Senior Researcher in Financial Fraud Detection, Risk Scoring Systems, and Decision Engines  

---

## 1. Executive Summary

This report presents a formal decision system evaluation and sensitivity analysis of the **Risk Scoring Engine**.

The evaluation analyzed signal monotonicity, marginal feature sensitivity ($\partial S / \partial x$), rule contributions, hybrid ML vs heuristic rule interaction ($25\%\text{ ML} : 75\%\text{ Rules}$), and score continuity.

---

## 2. Signal Monotonicity & Marginal Sensitivity Matrix

For a default weight configuration ($\sum w_k = 1.0$), the marginal contribution of signal $k$ to the composite score $S \in [0, 1000]$ is governed by:
$$\frac{\partial S}{\partial s_k} = 1000.0 \cdot w_k$$

| Signal Evaluator | Weight ($w_k$) | Input Parameter ($x$) | Signal Gradient ($\partial s_k / \partial x$) | Score Sensitivity ($\partial S / \partial x$) | Monotonicity Status |
|:---|:---:|:---|:---:|:---:|:---:|
| **ML Prediction** | $0.25$ ($25\%$) | Probability $p_{\text{ml}} \in [0, 1]$ | $1.0$ | **$+250.0$** / unit $p$ | 🟢 Monotonic Non-Decreasing |
| **Velocity Ramp** | $0.15$ ($15\%$) | Rate $v \in [2, 10]$ txns/hr | $1/8 = 0.125$ | **$+18.75$** / txn/hr | 🟢 Monotonic Non-Decreasing |
| **Merchant Reputation** | $0.10$ ($10\%$) | Merchant Score $m \in [0, 1]$ | $0.60$ | **$+60.0$** / unit $m$ | 🟢 Monotonic Non-Decreasing |
| **Country Risk** | $0.10$ ($10\%$) | FATF Risk $c \in [0, 1]$ | Discrete Lookup | Discrete Step Change | 🟢 Monotonic Discrete |
| **Device Anomaly** | $0.08$ ($8\%$) | Channel Score $d \in [0, 1]$ | Discrete Lookup | Discrete Step Change | 🟢 Monotonic Discrete |
| **Customer History** | $0.10$ ($10\%$) | Trust Score $h \in [0, 1]$ | $-1.0$ | **$-100.0$** / unit $h$ | 🟢 Monotonic Non-Increasing |
| **Previous Alerts** | $0.08$ ($8\%$) | Count $cnt \in [0, 5]$ | $1/5 = 0.20$ | **$+16.0$** / alert | 🟢 Monotonic Non-Decreasing |
| **Chargeback Rate** | $0.07$ ($7\%$) | Rate $r \in [0, 0.10]$ | $10.0$ | **$+700.0$** / unit $r$ ($+7.0$ / $\%$) | 🟢 Monotonic Non-Decreasing |
| **Behavior Z-Score** | $0.07$ ($7\%$) | Z-Score $z \in [1, 4]$ | $1/3 = 0.333$ | **$+23.33$** / $1\sigma$ deviation | 🟢 Monotonic Non-Decreasing |

---

## 3. Discontinuity & Threshold Cliff Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│               TENURE PENALTY DISCONTINUITY CLIFF (Day 29 -> 30)         │
├─────────────────────────────────────────────────────────────────────────┤
│ Account Age < 30 Days : +0.30 Risk Surcharge (adds +30.0 score points)  │
│ Account Age ≥ 30 Days :  0.00 Risk Surcharge                            │
│                                                                         │
│ Score (Day 29) ───┐ (-30.0 points)                                      │
│                   └─── Score (Day 30)                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

* **Observed Discontinuity:** Crossing Account Age Day 29 to Day 30 produces an instantaneous step-function drop of **$-30.0$ score points** ($w_{\text{hist}} = 0.10 \times 1000 \times 0.30 = 30.0$).
* **Decision System Justification:** This threshold cliff represents an intentional rule-based business policy (new account fraud surcharge). It ensures synthetic accounts under 30 days old remain under elevated scrutiny.

---

## 4. ML Prediction vs. Heuristic Rule Interactions

The decision architecture implements a hybrid governance model:
$$\text{Score} = 250 \cdot s_{\text{ml}} + 750 \cdot s_{\text{rules}}$$

1. **ML Model Alone Cannot Escalated to Critical Tier:**
   Even if ML probability $p_{\text{ml}} = 1.0$ ($s_{\text{ml}} = 1.0$), if all heuristic rules evaluate to zero risk ($s_{\text{rules}} = 0$), the composite score is $250.0$ (`low` tier).
2. **Heuristic Rules Alone Can Reach High Tier:**
   If all 8 heuristic rules saturate to maximum risk ($s_{\text{rules}} = 1.0$), the composite score reaches $750.0$ (`high` tier), triggering automated investigation even if ML probability is $0.0$.
3. **Critical Tier Requirement ($\ge 800$):**
   To reach the `critical` tier ($\ge 800$), a transaction requires **both** high ML confidence ($p_{\text{ml}} \ge 0.50$) and multiple high-risk heuristic flags.

---

## 5. Conclusion

The `RiskScoringEngine` exhibits smooth, monotonic, and proportional sensitivity to input changes, with the single intentional step-function cliff occurring at the 30-day account age boundary.
