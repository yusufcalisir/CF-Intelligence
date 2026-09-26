# SHAP Explainability & Risk Attribution Specification

## 1. Problem Solved
Under financial regulations (e.g., EU AI Act high-risk credit/fraud profiling requirements, US Equal Credit Opportunity Act, and Model Risk Management SR 11-7), automated transaction rejections or alerts cannot operate as inscrutable black boxes. Investigators and compliance officers require human-interpretable feature attributions explaining **why** a transaction received an elevated risk score.

SHAP (SHapley Additive exPlanations; Lundberg & Lee, 2017) resolves this by computing unique, mathematically axiomatic local feature attributions rooted in cooperative game theory.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/explainability_service.py`](file:///backend/app/application/services/explainability_service.py)
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
- **Unit Tests**: [`backend/tests/unit/test_explainability_service.py`](file:///backend/tests/unit/test_explainability_service.py)
- **Latency Benchmark**: [`benchmarks/runners/run_latency_benchmark.py`](file:///benchmarks/runners/run_latency_benchmark.py)
- **Latency Output**: [`benchmarks/results/raw/latency_concurrency_benchmark.json`](file:///benchmarks/results/raw/latency_concurrency_benchmark.json)
