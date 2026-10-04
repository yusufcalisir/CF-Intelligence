# SHAP Explainability & Risk Attribution Specification

## 1. Problem Solved
Under financial regulations (e.g., EU AI Act high-risk credit/fraud profiling requirements, US Equal Credit Opportunity Act, and Model Risk Management SR 11-7), automated transaction rejections or alerts cannot operate as inscrutable black boxes. Investigators and compliance officers require human-interpretable feature attributions explaining **why** a transaction received an elevated risk score.

SHAP (SHapley Additive exPlanations; Lundberg & Lee, 2017) resolves this by computing unique, mathematically axiomatic local feature attributions rooted in cooperative game theory.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/explainability_service.py`](../../backend/app/application/services/explainability_service.py)
- **Mathematical Formulation**:
  For an input transaction $x \in \mathbb{R}^p$ with composite model prediction $f(x)$, SHAP attributes an additive score $\phi_i$ to each feature $i \in \{1, \dots, p\}$:

  $$f(x) = \phi_0 + \sum_{i=1}^p \phi_i(x)$$

  where $\phi_0 = \mathbb{E}[f(z)]$ is the baseline expected risk score across normal background transactions.
- **Classical Shapley Value**:

  $$\phi_i(x) = \sum_{S \subseteq \mathcal{F} \setminus \{i\}} \frac{|S|! \, (|\mathcal{F}| - |S| - 1)!}{|\mathcal{F}|!} \left[ f_x(S \cup \{i\}) - f_x(S) \right]$$

- **KernelExplainer Optimization**:
  Because calculating all $2^p$ feature subsets is computationally intractable during live transaction processing ($p=10 \implies 1024$ evaluations), CF-Intelligence utilizes `shap.KernelExplainer` with weighted linear regression over $M = 100$ sampled coalitions:

  $$\pi_x(z') = \frac{|\mathcal{F}| - 1}{\binom{|\mathcal{F}|}{|z'|} \cdot |z'| \cdot (|\mathcal{F}| - |z'|)}$$

- **Asynchronous Execution & Fast-Path Isolation**:
  To prevent SHAP sampling from blocking the async FastAPI event loop:
  1. High-frequency payment authorizations route through `/api/v1/predict/fast` (sub-15ms fast-path, raw score without full SHAP kernel).
  2. Full investigative queries route through `/api/v1/predict`, offloading SHAP computation to an isolated `asyncio.to_thread` worker pool.

---

## 3. Threat Model & Adversarial Stability
- **Attribution Tampering**: Black-box explanation methods can be fooled by adversarial perturbations. In CF-Intelligence, baseline distributions are locked to vetted in-distribution transactions, preventing out-of-distribution explanation manipulation.

---

## 4. Operational Limitations
- **Computational Latency**: Full SHAP evaluation requires $\approx 15\mathrm{ms} - 25\mathrm{ms}$ per transaction on modern CPU cores, which is $> 10\times$ the raw model forward pass latency ($< 1.5\mathrm{ms}$).
- **Feature Correlation**: In the presence of highly collinear banking features (e.g., `amount` and `account_balance_delta`), attribution can be split between correlated inputs.

---

## 5. Test Suite Verification
- **Mathematical Invariant Tests**: [`backend/tests/unit/test_shap_mathematical_invariants.py`](../../backend/tests/unit/test_shap_mathematical_invariants.py) (Additivity axiom across 100 real transactions, symmetry, dummy player, reproducibility, and fraud cluster top-3 consistency)
- **Unit & Hardening Tests**: [`backend/tests/unit/test_explainability_service.py`](../../backend/tests/unit/test_explainability_service.py), [`backend/tests/unit/test_explainability_hardening.py`](../../backend/tests/unit/test_explainability_hardening.py)
- **Latency Benchmark**: [`benchmarks/runners/run_latency_benchmark.py`](../../benchmarks/runners/run_latency_benchmark.py)
- **Latency Output**: [`benchmarks/results/raw/latency_concurrency_benchmark.json`](../../benchmarks/results/raw/latency_concurrency_benchmark.json)

---

## 6. Counterfactual Engine Domain Feasibility & Re-Inference Verification

### 6.1 Problem Solved & Operational Objective
While SHAP provides local feature attribution for what caused an elevated risk score, compliance officers and fraud investigators require actionable recourse guidance: **"What minimal, feasible transaction modifications would clear this transaction or lower its risk below the decision threshold $\tau^*$?"**

In financial domains, naive counterfactual algorithms (such as unconstrained gradient descent or nearest-neighbor perturbation) produce invalid or non-actionable suggestions—e.g., suggesting an account change its opening date, altering historical SAR filings, or setting transaction amounts to negative values. The CF-Intelligence Counterfactual Engine resolves this by enforcing strict domain boundary guardrails, immutable attribute locks, and live model re-inference verification.

- **Location**: [`backend/app/application/services/counterfactual_service.py`](../../backend/app/application/services/counterfactual_service.py)
- **Integration**: [`backend/app/application/services/explainability_service.py`](../../backend/app/application/services/explainability_service.py)
- **Frontend Explorer**: [`frontend/src/components/cases/CounterfactualExplorer.tsx`](../../frontend/src/components/cases/CounterfactualExplorer.tsx)

### 6.2 Mathematical Formulation & Sparsity Optimization
Given an original transaction feature vector $x \in \mathcal{X} \subset \mathbb{R}^p$, original risk score $S(x) \in [0.0, 1.0]$, and target clearance threshold $\tau^* < S(x)$ (typically $\tau^* = 0.50$ for `ALLOW` or $\tau^* = 0.60$ for `REQUIRE_MFA`), the counterfactual optimization problem is formulated as finding a counterfactual vector $x^* \in \mathcal{X}$ satisfying:

$$x^* = \arg\min_{x' \in \mathcal{D}_{\mathrm{feas}}} \left[ \lambda \cdot \mathcal{L}_{\mathrm{score}}(S(x'), \tau^*) + \|\mathbf{w} \odot (x' - x)\|_0 + \alpha \|\mathbf{w} \odot (x' - x)\|_1 \right]$$

subject to:

$$S(x') \le \tau^*$$

$$x'_j = x_j \quad \forall j \in \mathcal{I}_{\mathrm{immutable}}$$

$$x'_k \in \mathrm{Range}(k) \quad \forall k \in \mathcal{M}_{\mathrm{mutable}}$$

where:
- $\mathcal{I}_{\mathrm{immutable}}$ is the set of protected immutable features (e.g., entity identity, account age, historical chargebacks).
- $\mathcal{M}_{\mathrm{mutable}}$ is the set of actionable features (e.g., amount, velocity, authentication channel).
- $\|\cdot\|_0$ enforces minimal perturbation sparsity (minimizing the number of feature changes required).
- $\mathcal{D}_{\mathrm{feas}}$ is the domain-feasible subspace defined by banking guardrails.

### 6.3 Attribute Immutability Invariant
Under banking compliance standards, historical facts, customer demographics, and cryptographically verified identities cannot be altered in counterfactual recourse. Any proposed perturbation attempting to alter an immutable feature is rejected with an `ImmutableFeatureViolationError`:

| Feature Identifier | Immutability Rationale | Violation Impact |
| :--- | :--- | :--- |
| `timestamp`, `account_created_at` | Historical temporal event integrity | Temporal forging / non-actionable |
| `account_age_days`, `age`, `date_of_birth` | Monotonic real-time KYC attributes | Impossible real-world modification |
| `customer_id`, `customer_national_id` | Core identity anchor | Identity substitution attack |
| `entity_hash`, `bank_id`, `jurisdiction` | Cryptographic ledger & tenant identifier | Cross-tenant spoofing |
| `historical_alerts_count`, `chargeback_history` | Retrospective ledger risk facts | Ledger history tampering |
| `customer_risk_profile`, `prior_sar_filings` | Regulatory compliance records | BSA/AML compliance evasion |
| `kyc_verification_level` | Verified sovereign identity tier | Unauthenticated privilege escalation |

### 6.4 Domain Feasibility Guardrails
For mutable attributes, values must fall within strictly defined real-world banking constraints:

| Mutable Feature | Guardrail Type | Feasibility Range / Valid Set |
| :--- | :--- | :--- |
| `transaction_amount` | Continuous Range | $[0.01, 10{,}000{,}000.00]\text{ USD}$ |
| `hour_of_day` | Discrete Range | $\{0, 1, \dots, 23\}$ |
| `device_trust_score` | Continuous Probability | $[0.0, 1.0]$ |
| `network_risk_score` | Continuous Probability | $[0.0, 1.0]$ |
| `velocity_1h`, `velocity_24h` | Non-negative Count | $[0.0, 1000.0]$ |
| `mfa_authenticated` | Discrete Boolean | $\{0, 1\}$ or $\{\text{True}, \text{False}\}$ |
| `country_code` | Categorical Allowed Set | ISO 3166-1 alpha-2 (`US`, `GB`, `DE`, `FR`, `CA`, `CH`, `JP`, etc.) |
| `merchant_category` | Categorical Allowed Set | Standard ISO MCC codes (`5411`, `6011`, `5732`, `7995`, etc.) |
| `device_type` | Categorical Allowed Set | Approved channels (`mobile_app`, `web_browser`, `pos_terminal`, `atm`) |

### 6.5 Live Model Re-Inference Verification
To eliminate false confidence from static heuristics, every counterfactual recommendation undergoes live forward-pass evaluation through the active [`RiskScoringEngine`](../../backend/app/application/services/risk_scoring_engine.py). A counterfactual is certified only when:

1. **Monotonic Risk Reduction**: $S(x') < S(x)$ (the risk score strictly decreases).
2. **Threshold Satisfaction**: $S(x') \le \tau^*$ (the risk score reaches or drops below target clearance).
3. **Policy Disposition Flip**: The policy disposition flips from `BLOCK_TRANSACTION` or `HOLD_FOR_REVIEW` to `ALLOW` or `REQUIRE_MFA`.
4. **Feasibility Validation**: `validate_counterfactual_transition(x, x') == True`.

### 6.6 Verification Test Suites
- **Constraint & Boundary Tests**: [`backend/tests/unit/test_counterfactual_constraints.py`](../../backend/tests/unit/test_counterfactual_constraints.py) (27 tests covering immutable attribute enforcement, domain feasibility bounds, invalid category rejection, re-inference prediction flips, and $L_0$ sparsity verification)
- **Integration Tests**: [`backend/tests/unit/test_counterfactuals.py`](../../backend/tests/unit/test_counterfactuals.py) (6 tests verifying service-level orchestration, backward compatibility, and error handling)
- **Frontend Component Tests**: [`frontend/src/components/cases/__tests__/CounterfactualExplorer.test.tsx`](../../frontend/src/components/cases/__tests__/CounterfactualExplorer.test.tsx) (4 Vitest tests covering interactive recourse exploration, slider bounds, locked immutable fields, and re-inference telemetry)

