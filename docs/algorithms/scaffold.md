# SCAFFOLD Algorithm Specification

## 1. Problem Solved
SCAFFOLD (Stochastic Controlled Averaging for Federated Learning; Karimireddy et al., 2020) mathematically overcomes "client drift" caused by extreme Non-IID data distributions among banking participants.

In standard FedAvg, when local data distributions differ ($\nabla F_i(w^*) \neq \nabla F_j(w^*)$), local updates pull models towards individual client optima rather than the global optimum. SCAFFOLD introduces client and server control variates to estimate and cancel out client drift directions.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/fl_engine.py`](file:///backend/app/application/services/fl_engine.py)
- **Control Variates**:
  - Global control variate: $c \in \mathbb{R}^d$
  - Client-specific control variate: $c_k \in \mathbb{R}^d$ for each Bank $k$.
- **Local Client Update**:
  At round $t$, Bank $k$ receives $w_t$ and $c$. For each local SGD step on batch $\mathcal{B}$:

  $$w \leftarrow w - \eta_l \left( \nabla F_k(w; \mathcal{B}) - c_k + c \right)$$

  The term $(c - c_k)$ corrects the local gradient direction toward the global gradient direction.
- **Variate Synchronization**:
  At the end of $K$ local steps with total step count $S = E \times \frac{n_k}{|\mathcal{B}|}$:

  $$c_k^+ = c_k - c + \frac{1}{S \eta_l} (w_t - w_k)$$

  $$\Delta c_k = c_k^+ - c_k, \quad \Delta w_k = w_k - w_t$$

  The server aggregates:

  $$w_{t+1} = w_t + \frac{\eta_g}{|\mathcal{S}|} \sum_{k \in \mathcal{S}} \Delta w_k, \quad c_{t+1} = c_t + \frac{1}{K} \sum_{k \in \mathcal{S}} \Delta c_k$$

---

## 3. Threat Model & Security Assumptions
- **State Persistence**: Bank nodes and server must maintain stateful control variates $c_k$ across training rounds.
- **Communication Cost**: Transmits double the payload per round ($w$ parameters + $c$ control variates) compared to FedAvg.

---

## 4. Operational Limitations
- **Memory Overhead**: Both banking clients and consortium coordinator must store control variates matching parameter vector size $d$.
- **Dropout Sensitivity**: If clients drop out frequently, stale control variates can introduce transient estimation errors until refreshed.

---

## 5. Non-IID Dirichlet Sweep Empirical Evaluation

The platform evaluates SCAFFOLD against FedAvg and FedProx under varying degrees of Dirichlet label and feature skew $\alpha \in \{0.1, 0.5, 1.0\}$:
- **Experiment Runner**: [`experiments/ablations/dirichlet_sweep.py`](file:///experiments/ablations/dirichlet_sweep.py)
- **Empirical Dossier**: [`experiments/ablations/audit_dossier.md`](file:///experiments/ablations/audit_dossier.md)
- **Publication Figure**: [`docs/figures/benchmark_fl_convergence.png`](file:///docs/figures/benchmark_fl_convergence.png)

### Empirical Results Summary across 10 Federation Rounds

| Skew Regime | Dirichlet $\alpha$ | SCAFFOLD PR-AUC | SCAFFOLD ROC-AUC | Validation Loss | Communication (MB) | Control Variate Overhead |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pathological Extreme Skew** | $\alpha = 0.1$ | **0.2551** | 0.8600 | 0.0976 | 0.294 MB | $2.0\times$ bandwidth vs FedAvg |
| **Moderate Consortium Skew** | $\alpha = 0.5$ | **0.2196** | 0.9175 | 0.0969 | 0.294 MB | Corrected local drift direction |
| **Mild Statistical Skew** | $\alpha = 1.0$ | **0.2004** | 0.8901 | 0.1074 | 0.294 MB | Steady asymptotic convergence |

*Key Takeaway*: SCAFFOLD successfully bounds client drift under non-IID heterogeneity by aligning local gradient updates $(g_k - c_k + c)$ with the consortium global consensus gradient. While it incurs double the payload per round ($92.30\text{ KB/round}$ per client), it achieves accelerated convergence under extreme statistical skew.

---

## 6. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_fl_engine.py`](file:///backend/tests/unit/test_fl_engine.py)
- **Dirichlet Sensitivity Tests**: [`backend/tests/unit/test_dirichlet_sweep.py`](file:///backend/tests/unit/test_dirichlet_sweep.py)
- **Benchmark Runner**: [`benchmarks/runners/run_fl_benchmark.py`](file:///benchmarks/runners/run_fl_benchmark.py)
