# Master Scientific Verification Roadmap — Risk Scoring & Decision Engine Subsystem

**Subsystem:** Risk Scoring Engine, Policy AST Evaluator & Decision Infrastructure  
**Repository:** Privacy-preserving Cross-Bank Fraud Detection using Federated Learning  
**Date:** August 2026  
**Auditor:** Senior Researcher in Financial Fraud Detection, Risk Scoring Systems, and Scientific Software Verification  

---

## Executive Overview

This roadmap defines a 5-phase scientific verification framework for the **Risk Scoring & Decision Engine** subsystem. Every mathematical formulation, signal evaluator, weighting scheme, AST policy evaluator, and system invariant is mapped to specific verification methodologies with scientific justifications.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                 5-PHASE SCIENTIFIC VERIFICATION ROADMAP                 │
├─────────────────────────────────────────────────────────────────────────┤
│ Phase 1: Independent Mathematical Reference Verification                │
│   └── Pure-Python reference model comparison (Max Abs Error = 0.00e+00) │
│ Phase 2: Property-Based Hypothesis Invariant Testing                    │
│   └── 500+ randomized scenarios across 6 core mathematical invariants   │
│ Phase 3: Adversarial Robustness & Failure Injection Testing             │
│   └── 10 failure injection stress scenarios                             │
│ Phase 4: Sensitivity & Decision System Analysis                         │
│   └── Gradient derivatives ∂S/∂x & tenure cliff profiling               │
│ Phase 5: Scalability & Performance Benchmarking                         │
│   └── TPS throughput, latency (µs/txn), and AST rule screening rate     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Roadmap Phases

### Phase 1: Independent Mathematical Reference Verification
* **Objective:** Compare production scoring outputs against pure-Python reference formulations without production code reuse.
* **Target Components:** Composite score calculation, 9 signal evaluators, scale mapping $[0, 1000]$.
* **Methodology:**
  1. Construct pure reference functions `ref_eval_*` for all 9 risk signals.
  2. Implement `ref_composite_score` with exact floating-point rounding.
  3. Evaluate 100 synthetic transaction scenarios and compare production outputs against reference outputs.
* **Metrics:** Maximum Absolute Error ($\text{MAE} = \max |S_{\text{prod}} - S_{\text{ref}}|$) and Maximum Relative Error ($\text{MRE}$).
* **Rationale:** Establishes ground-truth numerical equivalence, proving zero implementation drift or unintended side effects.

---

### Phase 2: Property-Based Hypothesis Testing
* **Objective:** Verify global mathematical invariants across thousands of randomized parameter spaces.
* **Target Components:** Score boundedness, weight scale invariance, ML monotonicity, risk tier completeness, explainability sorting.
* **Invariants Verified:**
  - **P1 Boundedness:** $\forall \text{inputs}, \ 0.0 \le S \le 1000.0$.
  - **P2 Scale Invariance:** $\forall c > 0, \ \text{Score}(W) \equiv \text{Score}(c \cdot W)$.
  - **P3 Monotonicity:** $p_1 \ge p_2 \implies \text{Score}(p_1) \ge \text{Score}(p_2)$.
  - **P4 Partition Completeness:** Every score maps uniquely to exactly one disjoint tier (`minimal` to `critical`).
  - **P5 Explainability Sorting:** Top signals are sorted in strict descending order of $w_k \cdot s_k$.
  - **P6 Payload Fallback:** Missing payload keys execute without throwing `KeyError`.
* **Rationale:** Uncovers hidden edge-case bugs that fixed static unit tests miss.

---

### Phase 3: Adversarial Robustness & Failure Injection Testing
* **Objective:** Attempt to break every scoring rule and evaluate system resilience under hostile inputs.
* **Scenarios Evaluated:**
  1. Empty transaction payload (`{}`).
  2. Malformed non-dict payload (`"not_a_dict"`).
  3. Floating-point NaN value injection.
  4. Floating-point Infinite (+Inf / -Inf) injection.
  5. Unknown merchant category fallback.
  6. Country code casing normalization (`"kp"` vs `"KP"`).
  7. Unsupported device channel fallback.
  8. Out-of-bounds customer history scores.
  9. Extremely large numeric values ($1\text{e}308$).
  10. Malformed AST policy condition evaluation.
* **Rationale:** Guarantees production stability, fault isolation, and zero unhandled application crashes.

---

### Phase 4: Sensitivity & Decision System Analysis
* **Objective:** Analyze score gradients $\partial S / \partial x$, feature attributions, and heuristic rule interactions.
* **Analysis Vectors:**
  - ML Prediction Sensitivity ($\partial S / \partial p_{\text{ml}}$).
  - Velocity Ramp Sensitivity ($\partial S / \partial v$).
  - Tenure Penalty Cliff (Day 29 to Day 30 transition).
  - Hybrid $25\%\text{ ML} : 75\%\text{ Rules}$ governing ratio.
* **Rationale:** Provides explainability insights required for regulatory compliance (EU AI Act Article 13).

---

### Phase 5: Scalability & Performance Benchmarking
* **Objective:** Profile execution latency, throughput, and memory consumption under high transaction loads.
* **Target Load:** $N = 50,000$ transactions and $R = 1,000$ AST policy rules.
* **Metrics Tracked:**
  - Single transaction scoring latency ($\mu\text{s}$/txn).
  - Transaction processing throughput (TPS).
  - Scoring memory RSS footprint (MB).
  - AST rule screening rate (rules/sec).
* **Rationale:** Ensures the decision engine satisfies sub-10 millisecond real-time payment authorization SLAs.

---

*This roadmap completes the strategic verification planning for the Risk Scoring Engine.*
