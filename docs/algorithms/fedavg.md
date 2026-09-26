# Federated Averaging (FedAvg) Algorithm Specification

## 1. Problem Solved
Federated Averaging (McMahan et al., 2017) resolves the distributed optimization problem of fitting a global fraud classification model across $K$ decentralized banking institutions without transferring or pooling raw transaction data.

Mathematically, it seeks to minimize the global empirical objective:

$$\min_{w \in \mathbb{R}^d} f(w) \quad \text{where} \quad f(w) \triangleq \sum_{k=1}^K \frac{n_k}{N} F_k(w)$$

where $n_k$ is the number of local transaction samples at Bank $k$, $N = \sum_{k=1}^K n_k$, and $F_k(w) = \frac{1}{n_k} \sum_{i \in \mathcal{D}_k} \ell(w; x_i, y_i)$ is Bank $k$'s local empirical risk.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/fl_engine.py`](file:///backend/app/application/services/fl_engine.py)
- **Aggregation Formulation**:
  At round $t$, the central coordinator broadcasts global weights $w_t$. Each participant bank executes $E$ local epochs of mini-batch SGD:

  $$w_k^{t+1} \leftarrow \mathrm{ClientUpdate}(k, w_t)$$

  The coordinator receives model update vectors $\Delta w_k = w_k^{t+1} - w_t$ and computes the weighted aggregate:

  $$w_{t+1} = w_t + \sum_{k \in \mathcal{S}_t} \frac{n_k}{\sum_{j \in \mathcal{S}_t} n_j} \Delta w_k$$

  where $\mathcal{S}_t \subseteq \{1, \dots, K\}$ is the active cohort selected for round $t$.

---

## 3. Threat Model & Security Assumptions
- **Honest-but-Curious Coordinator**: FedAvg assumes the coordinator faithfully aggregates weights but may inspect unmasked gradients.
- **Privacy Limitation**: Raw FedAvg without Differential Privacy or Secure Aggregation is vulnerable to gradient inversion attacks (Deep Leakage from Gradients) and membership inference.
- **Robustness Limitation**: Assumes all selected clients are honest ($f = 0$ malicious clients). A single adversarial client can poison $w_{t+1}$ by submitting arbitrarily scaled gradients.

---

## 4. Non-IID Dirichlet Client Partitioning & Client Drift Dynamics

Cross-bank federated fraud detection operates inherently under heterogeneous data distributions across financial institutions. In **CF-Intelligence**, this institutional heterogeneity is mathematically modeled using a Dirichlet distribution $\operatorname{Dir}(\alpha \cdot \mathbf{1}_K)$ implemented in [`experiments/paysim/partitioner.py`](file:///experiments/paysim/partitioner.py).

### 4.1 Dirichlet Allocation Formulation

For each binary transaction class $c \in \{0, 1\}$ (legitimate transactions and fraudulent transfers), a probability vector $\mathbf{q}_c = (q_{c,1}, \dots, q_{c,K})$ is sampled independently:

$$\mathbf{q}_c \sim \operatorname{Dir}(\alpha \cdot \mathbf{1}_K), \quad \text{where } \sum_{k=1}^K q_{c,k} = 1, \; q_{c,k} \ge 0$$

where $\alpha > 0$ denotes the Dirichlet concentration parameter and $K$ is the number of participating banking institutions (default $K=3$: Bank A, Bank B, Bank C).

The number of samples of class $c$ assigned to institution $k$ is $n_{c,k} = \lfloor q_{c,k} N_c \rfloor$, with residual samples allocated to maintain exact dataset conservation:

$$\sum_{k=1}^K n_{c,k} = N_c, \quad \mathcal{D}_j \cap \mathcal{D}_k = \emptyset \quad \forall j \neq k$$

### 4.2 Impact of Concentration Parameter $\alpha$ on Client Drift

The gradient variance between local client objectives and the global consortium objective is directly bounded by the Dirichlet concentration:

$$\mathbb{E}_{k \sim \mathcal{P}_K} \left[ \left\| \nabla F_k(w) - \nabla f(w) \right\|^2 \right] \le G^2(\alpha)$$

- **$\alpha = 0.1$ (Extreme Non-IID Heterogeneity)**: Induces severe label distribution skew and high Kullback-Leibler divergence ($D_{\mathrm{KL}}(P_k \parallel P_{\mathrm{global}}) \gg 1.0$). Certain banks encounter negligible fraud while others experience high alert volume. Local SGD directions diverge sharply, resulting in client drift that accelerates the need for proximal regularization (FedProx $\mu > 0$) or control variates (SCAFFOLD).
- **$\alpha = 0.5$ (Realistic Consortium Skew)**: Calibrated to empirical banking consortium structures, simulating volume differences between Tier-1 retail banks, regional commercial lenders, and digital challenger banks.
- **$\alpha = 1.0$ (Uniform Dirichlet / Moderate Heterogeneity)**: Approaching balanced participation where all institutions observe representative cross-bank fraud signals.

### 4.3 Statistical Divergence Metrics

The platform quantifies client distribution divergence via Kullback-Leibler (KL) divergence and Total Variation Distance (TVD):

$$D_{\mathrm{KL}}(P_k \parallel P_{\mathrm{global}}) = \sum_{c \in \{0, 1\}} P_k(c) \ln \frac{P_k(c) + \epsilon}{P_{\mathrm{global}}(c) + \epsilon}$$

$$\operatorname{TVD}(P_k, P_{\mathrm{global}}) = \frac{1}{2} \sum_{c \in \{0, 1\}} \lvert P_k(c) - P_{\mathrm{global}}(c) \rvert$$

---

## 5. Operational Limitations
- **Client Drift under Extreme Skew**: When local label distributions diverge significantly ($\alpha \le 0.1$ in Dirichlet skew), vanilla FedAvg weights oscillate between local minima, degrading global test PR-AUC.
- **System Stragglers**: In synchronous FedAvg, round completion latency is bounded by the slowest banking node.

---

## 6. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_fl_engine.py`](file:///backend/tests/unit/test_fl_engine.py), [`backend/tests/unit/test_dirichlet_partition.py`](file:///backend/tests/unit/test_dirichlet_partition.py)
- **Partitioner Implementation**: [`experiments/paysim/partitioner.py`](file:///experiments/paysim/partitioner.py)
- **Benchmark Runner**: [`benchmarks/runners/run_fl_benchmark.py`](file:///benchmarks/runners/run_fl_benchmark.py)
