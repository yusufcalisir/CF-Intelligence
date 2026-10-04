# Byzantine Robustness & Adversarial Defense Specification

## 1. Problem Solved
Byzantine Robustness protects federated consortium models against malicious, compromised, or faulty banking nodes attempting to:
1. **Model Poisoning**: Prevent global model convergence by submitting catastrophic gradient updates.
2. **Backdoor Injection**: Embed subtle triggers allowing specific fraud syndicates to bypass risk scoring while maintaining high validation accuracy on normal transactions.
3. **Sign Inversion**: Invert update signs to negate fraud pattern learning.
4. **Extreme Scaled Updates**: Amplify update magnitude ($100\times$) to dominate the global average.
5. **Targeted Label Flipping**: Train local updates on inverted fraud labels to degrade minority recall.

---

## 2. Implementation in CF-Intelligence
- **Domain Modules**: [`backend/app/domain/byzantine_defense.py`](../../backend/app/domain/byzantine_defense.py), [`backend/app/domain/attack_injector.py`](../../backend/app/domain/attack_injector.py), and [`backend/app/domain/spectral_defense.py`](../../backend/app/domain/spectral_defense.py)
- **FL Orchestration Engine**: [`backend/app/application/services/fl_engine.py`](../../backend/app/application/services/fl_engine.py)

### 2.1 Coordinate-wise Trimmed Mean
For each coordinate $j \in \{1, \dots, d\}$, the coordinator sorts the client updates $\{ \Delta w_{1,j}, \dots, \Delta w_{K,j} \}$, trims the smallest $\beta$ and largest $\beta$ fractions (where $\beta < 0.5$), and computes the mean of the remaining updates:

$$(\Delta w_{\mathrm{TM}})_j = \frac{1}{K - 2\lfloor \beta K \rfloor} \sum_{i=\lfloor \beta K \rfloor + 1}^{K - \lfloor \beta K \rfloor} \Delta w_{(i), j}$$

### 2.2 Krum & Multi-Krum (Blanchard et al., 2017)
Krum identifies the single client update $\Delta w_i$ that minimizes the sum of squared Euclidean distances to its $K - f - 2$ nearest neighbors, where $f$ is the maximum number of tolerated Byzantine nodes:

$$i^* = \arg\min_{i} \sum_{j \in \mathcal{N}_i} \|\Delta w_i - \Delta w_j\|_2^2$$

where $\mathcal{N}_i$ contains the $K - f - 2$ closest updates to $\Delta w_i$ in Euclidean distance.
**Multi-Krum** averages the top $m$ candidates satisfying this criterion.

### 2.3 Bulyan (El Mhamdi / Guerraoui et al., 2018)
Bulyan combines Krum selection with coordinate-wise trimmed mean to defeat colluding adversaries in high-dimensional parameter spaces:
1. Iteratively applies Krum to select a subset $\mathcal{C}$ of size $\theta = K - 2f$ candidates.
2. Computes the coordinate-wise trimmed mean on $\mathcal{C}$, trimming $2f$ values per coordinate.

### 2.4 Spectral SVD Defense
Computes singular value decomposition on the centered client update matrix:

$$\mathbf{M} = [\Delta w_1 - \bar{w}, \dots, \Delta w_K - \bar{w}]^T = \mathbf{U} \mathbf{\Sigma} \mathbf{V}^T$$

Updates with projection scores exceeding $3\sigma$ along the principal singular vector are quarantined as poisoned backdoor candidates.

---

## 3. Theoretical Guarantees & Breakdown Points

| Aggregation Algorithm | Theoretical Invariant | Max Tolerable $f$ ($N=10$) | Breakdown Behavior | Primary Defense Capability |
| :--- | :---: | :---: | :---: | :--- |
| **FedAvg** | $f = 0$ | $f = 0$ (0%) | Catastrophic collapse on $f \ge 1$ | None (honesty assumed) |
| **Coordinate Median** | $f < \frac{n}{2}$ | $f \le 4$ (40%) | Degrades under directional sign flips | High-magnitude outlier resistance |
| **Trimmed Mean (20%)** | $f \le \beta n$ | $f \le 2$ (20%) | Degrades when $f > \lfloor \beta n \rfloor$ | Robust coordinate averaging |
| **Krum** | $2f + 2 < n$ | $f \le 3$ (30%) | Breaks at $2f + 2 \ge n$ | Distance-based cluster selection |
| **Bulyan** | $n \ge 4f + 3$ | $f \le 1$ (10%) | Requires larger quorum ($n \ge 7$) | Collusion-resistant high-d defense |

---

## 4. Empirical Evaluation under 20% Byzantine Contamination ($f=2, N=10$)

Empirically validated on 2,000 holdout transactions across 4 adversarial attack modalities:

| Defense Strategy | Sign-Flip PR-AUC | Scaled Update PR-AUC | Gaussian Noise PR-AUC | Label Poisoning PR-AUC | Cosine Alignment | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **FedAvg (Unprotected)** | `0.9985` | `0.2772` | `0.1286` | `0.9982` | `+0.9887` | **BROKEN** |
| **Coordinate Median** | `0.9979` | `0.9982` | `0.9941` | `0.9986` | `+0.9556` | **RESILIENT** |
| **Trimmed Mean (20%)** | `0.9982` | `0.9979` | `0.9949` | `0.9993` | `+0.9564` | **RESILIENT** |
| **Krum (Blanchard et al.)** | `0.9962` | `0.9938` | `0.9923` | `0.9976` | `+0.8379` | **RESILIENT** |
| **Bulyan (El Mhamdi et al.)**| `0.9962` | `0.9938` | `0.9923` | `0.9976` | `+0.8379` | **RESILIENT** |

![Byzantine Resilience Benchmark](../../docs/figures/benchmark_byzantine_resilience.png)

---

## 5. Operational Limitations & Best Practices
- **Pairwise Distance Computation**: Krum requires $\mathcal{O}(K^2 \cdot d)$ Euclidean distance calculations. For massive models, coordinate-wise trimmed mean is computationally preferred.
- **Benign Heterogeneity Penalty**: Under severe Non-IID skew ($\alpha \le 0.1$), honest clients with specialized regional data may appear as outliers and be trimmed.
- **SecAgg Compatibility Boundary**: Non-linear Byzantine aggregators (Krum, Bulyan, Median) operate on plaintext updates and are algebraically incompatible with additive zero-sum pairwise masking; this boundary is enforced at consortium startup.

---

## 6. Test Suite & Verification Reference
- **Adversarial Poisoning Suite Runner**: [`experiments/byzantine/run_poisoning_suite.py`](../../experiments/byzantine/run_poisoning_suite.py)
- **Attack Injector Domain Module**: [`backend/app/domain/attack_injector.py`](../../backend/app/domain/attack_injector.py)
- **Byzantine Domain Engine & Breakdown Analyzer**: [`backend/app/domain/byzantine_defense.py`](../../backend/app/domain/byzantine_defense.py)
- **Unit & Branch Coverage Tests**: [`backend/tests/unit/test_byzantine_defense_branches.py`](../../backend/tests/unit/test_byzantine_defense_branches.py)
- **Spectral SVD Tests**: [`backend/tests/unit/test_spectral_defense.py`](../../backend/tests/unit/test_spectral_defense.py)
- **Interactive Scenarios Attack Test**: [`backend/tests/unit/test_attack_injector.py`](../../backend/tests/unit/test_attack_injector.py)
- **Benchmark Raw Results**: [`benchmarks/results/raw/byzantine_breakdown_analysis.json`](../../benchmarks/results/raw/byzantine_breakdown_analysis.json)
