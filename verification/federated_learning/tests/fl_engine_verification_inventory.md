# Verification Inventory — FederatedLearningEngine Subsystem

This document provides a comprehensive scientific audit and verification inventory of the `FederatedLearningEngine` module and its associated value objects, server optimizers, Byzantine defenses, privacy mechanisms, and simulation utilities.

---

## 1. Inventory Summary

* **Audited Subsystems:** 22 mathematical algorithms, optimizer states, privacy engines, and value objects.
* **Primary Source Code Files:**
  * `backend/app/application/services/fl_engine.py`
  * `backend/app/domain/spectral_defense.py`
  * `backend/app/domain/models.py` (`ModelWeights`, `AggregationMethod`)

---

## 2. Comprehensive Subsystem & Mathematical Inventory

### Component 1: Unweighted FedAvg Aggregator
* **Purpose:** Computes uniform arithmetic mean across client parameters (McMahan et al., 2017).
* **Mathematical Definition:**
  $$W_{\text{global}} = \frac{1}{N} \sum_{i=1}^N W_i$$
* **Expected Invariant:** Single client $N=1 \implies W_{\text{global}} = W_1$. $W_{\text{global}}$ lies strictly within the convex hull of $\{W_i\}$.
* **Implementation Risks:** Floating point rounding drift across large $N$.
* **Edge Cases:** Single client, zero-length parameter arrays.
* **Scientific Claim:** Implements unbiased FedAvg for IID client distributions.
* **Verification Methodology:** Reference contract test against exact Python `statistics.fmean()`.

---

### Component 2: Sample-Weighted FedAvg Aggregator
* **Purpose:** Weights client parameter vectors proportionally by local sample count $n_i$.
* **Mathematical Definition:**
  $$W_{\text{global}} = \sum_{i=1}^N p_i W_i \quad \text{where } p_i = \frac{n_i}{\sum_{j=1}^N n_j}$$
* **Expected Invariant:** Partition preservation $\sum_{i=1}^N p_i = 1$. Equal sample counts collapse to unweighted FedAvg.
* **Implementation Risks:** Integer division overflow or zero total sample sum ($\sum n_i = 0$).
* **Edge Cases:** Imbalanced sample ratios ($1:10^6$), zero samples ($n_i = 0$).
* **Scientific Claim:** Computes sample-proportional global expectation.
* **Verification Methodology:** Contract test against exact float dot-product reference.

---

### Component 3: FedAdam Adaptive Server Optimizer
* **Purpose:** Applies first and second-moment momentum tracking on pseudo-gradients at the server (Reddi et al., 2021).
* **Mathematical Definition:**
  $$\Delta_t = W_{\text{avg}} - W_t, \quad m_t = \beta_1 m_{t-1} + (1-\beta_1)\Delta_t, \quad v_t = \beta_2 v_{t-1} + (1-\beta_2)\Delta_t^2$$
  $$\hat{m}_t = \frac{m_t}{1-\beta_1^t}, \quad \hat{v}_t = \frac{v_t}{1-\beta_2^t}, \quad W_{t+1} = W_t + \eta \frac{\hat{m}_t}{\sqrt{\hat{v}_t} + \tau}$$
* **Expected Invariant:** $\hat{v}_t \ge 0$ for all $t$; zero updates ($\Delta_t = 0$) yield static weights $W_{t+1} = W_t$.
* **Implementation Risks:** Division by zero when $\sqrt{\hat{v}_t} + \tau \approx 0$.
* **Edge Cases:** Round $t=1$ bias correction initialization, zero pseudo-gradient $\Delta_t = \mathbf{0}$.
* **Scientific Claim:** Implements bias-corrected FedAdam server momentum update.
* **Verification Methodology:** Closed-form mathematical step evaluation.

---

### Component 4: FedAdaGrad Adaptive Server Optimizer
* **Purpose:** Accumulates historical squared pseudo-gradients to adapt per-coordinate server learning rates.
* **Mathematical Definition:**
  $$v_t = v_{t-1} + \Delta_t^2, \quad W_{t+1} = W_t + \eta \frac{\Delta_t}{\sqrt{v_t} + \tau}$$
* **Expected Invariant:** Monotonically non-decreasing variance accumulation $v_t \ge v_{t-1}$.
* **Implementation Risks:** Excessive learning rate decay for long training runs ($t \gg 10^3$).
* **Edge Cases:** Zero initial variance $v_0 = \mathbf{0}$.
* **Scientific Claim:** Implements exact FedAdaGrad accumulation.
* **Verification Methodology:** Reference contract test against analytical equations.

---

### Component 5: FedYogi Adaptive Server Optimizer
* **Purpose:** Prevents premature learning rate decay via sign-controlled variance tracking (Reddi et al., 2021).
* **Mathematical Definition:**
  $$v_t = v_{t-1} - (1-\beta_2)\mathrm{sign}(v_{t-1} - \Delta_t^2) \odot \Delta_t^2, \quad W_{t+1} = W_t + \eta \frac{m_t}{\sqrt{v_t} + \tau}$$
* **Expected Invariant:** $v_t \ge 0$ given non-zero initialization $v_0 = \tau^2 \mathbf{1}$.
* **Implementation Risks:** Incorrect sign function handling near numerical zero.
* **Edge Cases:** Identical squared pseudo-gradients $v_{t-1} = \Delta_t^2$.
* **Scientific Claim:** Implements sign-controlled Yogi variance adaptation.
* **Verification Methodology:** Property-based invariant validation.

---

### Component 6: Krum Byzantine-Robust Selection
* **Purpose:** Selects the single most representative client update minimizing distances to nearest neighbors (Blanchard et al., 2017).
* **Mathematical Definition:**
  $$i^* = \arg\min_{i} \sum_{j \in S_i} \|W_i - W_j\|^2 \quad \text{where } |S_i| = N - f - 2$$
  Dynamic parameterization: $f = \max(1, \min(1, \lfloor \frac{N-1}{2} \rfloor))$.
* **Expected Invariant:** Output $W_{\text{krum}} \in \{W_1, \dots, W_N\}$ (exact selection from honest set).
* **Implementation Risks:** $\mathcal{O}(N^2 d)$ pairwise distance computation latency.
* **Edge Cases:** Single outlier $10^{12}$ scale, $N < 5$ fallback bounds.
* **Scientific Claim:** Bounded output selection rejecting up to $f$ Byzantine workers.
* **Verification Methodology:** Adversarial outlier rejection testing.

---

### Component 7: Coordinate-wise Median Aggregator
* **Purpose:** Selects coordinate-wise median values across client updates (Yin et al., 2018).
* **Mathematical Definition:**
  $$[W_{\text{global}}]_k = \text{median}\Big( \{ [W_i]_k \}_{i=1}^N \Big)$$
* **Expected Invariant:** $50\%$ breakdown point under independent coordinate attacks. Translation invariant $\text{median}(W + c) = \text{median}(W) + c$.
* **Implementation Risks:** Sorting overhead $\mathcal{O}(N d \log N)$.
* **Edge Cases:** Even vs odd client counts $N$.
* **Scientific Claim:** $50\%$ breakdown robust estimation under IID settings.
* **Verification Methodology:** Property-based translation invariance tests.

---

### Component 8: Coordinate-wise Trimmed Mean
* **Purpose:** Removes $f$ largest and $f$ smallest values per parameter coordinate prior to averaging (Yin et al., 2018).
* **Mathematical Definition:**
  $$[W_{\text{global}}]_k = \frac{1}{N - 2f} \sum_{i=f+1}^{N-f} [W_{(i)}]_k$$
  Dynamic parameterization: $f = \max(1, \min(1, \lfloor \frac{N-1}{2} \rfloor))$.
* **Expected Invariant:** Extreme coordinates outside $[W_{(f+1)}, W_{(N-f)}]$ are completely ignored.
* **Implementation Risks:** Fallback to FedAvg when $N \le 2f$.
* **Edge Cases:** Extreme outlier values ($\pm 10^9$).
* **Scientific Claim:** Robust coordinate-wise averaging removing $f$ extreme values.
* **Verification Methodology:** Outlier injection robustness suite.

---

### Component 9: Bulyan Aggregation
* **Purpose:** Combines Krum selection with Trimmed Mean to defeat colluding Byzantine attackers (El Mhamdi et al., 2018).
* **Mathematical Definition:**
  Step 1: Select $\theta = N - 2f$ clients with lowest Krum scores.
  Step 2: Apply Trimmed Mean with $\text{trim\_f} = \max(0, (\theta - 1) // 4)$ on selected subset.
* **Expected Invariant:** Colluding attackers crafting close updates cannot bias the output vector.
* **Implementation Risks:** High execution time for $N \ge 100$.
* **Edge Cases:** $N < 7$ falling back to Krum subset selection.
* **Scientific Claim:** Defeats coordinated colluding attacks for $N \ge 4f + 3$.
* **Verification Methodology:** Multi-client colluding attack simulation.

---

### Component 10: SCAFFOLD Non-IID Drift Control
* **Purpose:** Corrects client drift in heterogeneous non-IID data distributions (Karimireddy et al., 2020).
* **Mathematical Definition:**
  $$W_{\text{global}} = \frac{1}{N} \sum_{i=1}^N W_i, \quad c_{\text{global}} \leftarrow c_{\text{global}} + \frac{1}{N} \sum_{i=1}^N \Delta c_i$$
* **Expected Invariant:** $c_{\text{global}}$ tracks global parameter drift across rounds.
* **Implementation Risks:** Uninitialized $c_{\text{global}}$ state arrays across simulation runs.
* **Edge Cases:** Zero client drift $\Delta c_i = \mathbf{0}$.
* **Scientific Claim:** Implements server FedAvg and control variate state tracking.
* **Verification Methodology:** Reference contract test against drift equations.

---

### Component 11: Leave-One-Out (LOO) Parameter Aggregation
* **Purpose:** Computes counterfactual marginal model updates $W_{-i}$ for Shapley data valuation.
* **Mathematical Definition:**
  $$W_{-i} = \frac{1}{\sum_{j \neq i} n_j} \sum_{j \neq i} n_j W_j$$
* **Expected Invariant:** $\frac{\partial W_{-i}}{\partial W_i} = \mathbf{0}$ (strict independence from excluded client $i$).
* **Implementation Risks:** Index out of bounds when $i \ge N$.
* **Edge Cases:** $N=1$ client (raising `ValueError`).
* **Scientific Claim:** Non-participating marginal model computation.
* **Verification Methodology:** Counterfactual invariance test.

---

### Component 12: GraphSAGE Parameter Aggregator & Validator
* **Purpose:** Validates and aggregates Graph Neural Network (GNN) embeddings and layer parameters across bank subgraphs.
* **Mathematical Definition:** Checks $\text{shape}(W_i) == \text{shape}(W_j)$ and $\text{len}(W_i) == \text{len}(W_j)$ prior to FedAvg.
* **Expected Invariant:** Strict rejection of mismatched layer dimensions or node feature shapes.
* **Implementation Risks:** Incomplete error handling on empty layer shapes.
* **Edge Cases:** Heterogeneous GNN architectures across banks.
* **Scientific Claim:** Dimension-safe GNN parameter aggregation.
* **Verification Methodology:** Shape mismatch fault injection.

---

### Component 13: EU AI Act Fairness Contingency Table Aggregator
* **Purpose:** Aggregates discrete confusion matrix counts (TP, FP, TN, FN) across demographic groups for compliance auditing.
* **Mathematical Definition:**
  $$C_{\text{global}} = \sum_{i=1}^N C_i \quad \text{where } C_i \in \mathbb{N}^{2 \times 2}$$
* **Expected Invariant:** Strict non-negativity $C_{\text{global}} \ge 0$ and additive exactness.
* **Implementation Risks:** Negative integer counts from corrupted payloads.
* **Edge Cases:** Zero count entries.
* **Scientific Claim:** Additive collation of fairness metrics.
* **Verification Methodology:** Discrete integer sum contract test.

---

### Component 14: Client Availability Markov Simulator
* **Purpose:** Simulates intermittent network connectivity and client dropouts across federated rounds.
* **Mathematical Definition:**
  Markov state transition: $P(\text{Online} \to \text{Offline}) = p_{\text{drop}}$, $P(\text{Offline} \to \text{Online}) = p_{\text{recon}} = 0.7$.
* **Expected Invariant:** Stationarity of Markov chain; $0 \le p_{\text{drop}} \le 1$.
* **Implementation Risks:** Non-deterministic RNG seeds causing non-reproducible runs.
* **Edge Cases:** $p_{\text{drop}} = 1.0$ (100% dropout), $p_{\text{drop}} = 0.0$ (100% availability).
* **Scientific Claim:** Valid Markovian client churn simulator.
* **Verification Methodology:** Monte Carlo chi-squared goodness-of-fit test.

---

### Component 15: Network Latency Uniform Simulator
* **Purpose:** Simulates network transport delay across distributed consortium nodes.
* **Mathematical Definition:**
  $$\tau_i \sim U(\text{min-ms}, \text{max-ms})$$
* **Expected Invariant:** $\text{min-ms} \le \tau_i \le \text{max-ms}$ for all drawn delays.
* **Implementation Risks:** Non-blocking async sleep blocking event loop if misused.
* **Edge Cases:** $\text{min-ms} == \text{max-ms}$.
* **Scientific Claim:** Uniform stochastic delay generator.
* **Verification Methodology:** Monte Carlo Kolmogorov-Smirnov test.

---

### Component 16: Zero-Sum Pairwise Masking (SecAgg Prototype)
* **Purpose:** Generates zero-sum pairwise random masks to protect local updates before aggregation.
* **Mathematical Definition:**
  $$\sum_{i=1}^N p_i m_i = \mathbf{0}$$
* **Expected Invariant:** $\text{FedAvg}(\{W_i + m_i\}) \equiv \text{FedAvg}(\{W_i\})$.
* **Implementation Risks:** Non-zero mask sum due to floating point rounding error.
* **Edge Cases:** $N=2$ pair masking.
* **Scientific Claim:** Exact zero-sum mask cancellation identity.
* **Verification Methodology:** Property-based zero-sum verification.

---

### Component 17: Model Poisoning Attack Simulator
* **Purpose:** Simulates untargeted Gaussian noise injection to audit Byzantine defenses.
* **Mathematical Definition:**
  $$\tilde{W}_{\text{poisoned}} = W_i + \mathcal{N}(\mathbf{0}, \sigma^2 \mathbf{I})$$
* **Expected Invariant:** Noise std-dev scales with parameter variance $\sigma = \text{std}(W_i)$.
* **Implementation Risks:** Injected Inf or NaN values destroying NumPy float operations.
* **Edge Cases:** $\sigma = 0$ (constant zero parameter vector).
* **Scientific Claim:** Controlled model poisoning generator.
* **Verification Methodology:** Monte Carlo Gaussian distribution audit.

---

### Component 18: FedAsync Asynchronous Aggregator
* **Purpose:** Aggregates stale client updates received out-of-order using exponential staleness attenuation (Xie et al., 2019).
* **Mathematical Definition:**
  $$S(\tau) = (1 + \tau)^{-\alpha}, \quad W_{t+1} = (1 - \gamma S(\tau)) W_t + \gamma S(\tau) W_{\text{client}}$$
* **Expected Invariant:** $0 < S(\tau) \le 1$; zero staleness ($\tau = 0$) gives maximum learning rate $\gamma$.
* **Implementation Risks:** Negative staleness $\tau < 0$ causing exploding weights.
* **Edge Cases:** Extreme staleness $\tau \gg 10^2$.
* **Scientific Claim:** Exponential staleness attenuated convex update interpolation.
* **Verification Methodology:** Reference contract test.

---

### Component 19: Median Absolute Deviation (MAD) Norm Defense
* **Purpose:** Filters heavy-tailed model update outliers based on median parameter norm deviations.
* **Mathematical Definition:**
  $$\text{MAD} = \text{median}(\|\|W_i\| - \text{median}(\|W\|)\|), \quad \text{Quarantine if } \|W_i\| > \text{median} + \kappa \cdot \text{MAD}$$
* **Expected Invariant:** Scale-invariant outlier detection.
* **Implementation Risks:** Division by zero when $\text{MAD} = 0$.
* **Edge Cases:** Homogeneous client updates (zero variance).
* **Scientific Claim:** Robust MAD-based update norm filtering.
* **Verification Methodology:** Outlier injection test suite.

---

### Component 20: Multi-Rank Spectral Anomaly Defense
* **Purpose:** Detects multi-subspace gradient backdoor injections using SVD power iteration and matrix deflation.
* **Mathematical Definition:**
  $$s_i = \sum_{r=1}^k |\langle \Delta W_i, v_r \rangle|^2 \quad (k=3)$$
* **Expected Invariant:** Captures multi-rank backdoors ($\text{rank} > 1$) evading top-1 power iteration.
* **Implementation Risks:** Non-convergence of power iteration on degenerate matrices.
* **Edge Cases:** $N < 3$ clients (falling back gracefully).
* **Scientific Claim:** SVD multi-rank subspace anomaly detection.
* **Verification Methodology:** Multi-rank backdoor matrix injection.

---

### Component 21: Gaussian Differential Privacy (DP) Engine
* **Purpose:** Clips client update L2 norms and adds Gaussian noise to guarantee Client-Level $(\epsilon, \delta)$-DP.
* **Mathematical Definition:**
  $$\bar{W}_i = W_i \cdot \min\left(1, \frac{C_{\text{max}}}{\|W_i\|_2}\right) + \mathcal{N}\left(\mathbf{0}, \sigma^2 C_{\text{max}}^2 \mathbf{I}\right)$$
* **Expected Invariant:** $\|\bar{W}_i - \text{noise}\|_2 \le C_{\text{max}}$.
* **Implementation Risks:** $C_{\text{max}} = 0$ causing division by zero.
* **Edge Cases:** Zero norm update vectors $\|W_i\| = 0$.
* **Scientific Claim:** Client-level Gaussian Differential Privacy bounds.
* **Verification Methodology:** Monte Carlo noise variance verification.

---

### Component 22: ModelWeights Immutable Value Object
* **Purpose:** Immutable container encapsulating parameter layer shapes and flattened weight arrays.
* **Mathematical Definition:**
  $$\text{flat\_weights} \in \mathbb{R}^d \quad \text{where } d = \prod_{l} \text{shape}(l)$$
* **Expected Invariant:** Product of layer dimensions equals total flat weight length $d$. Immutable upon initialization.
* **Implementation Risks:** Mismatch between `layer_shapes` product and `flat_weights` length.
* **Edge Cases:** 1D vectors, zero-dimensional scalar parameters.
* **Scientific Claim:** Type-safe, dimension-checked model weight value object.
* **Verification Methodology:** Invariant violation property testing.

---

*This document completes the comprehensive verification inventory for `FederatedLearningEngine`.*
