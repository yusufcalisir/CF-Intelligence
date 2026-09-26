# Byzantine Robustness & Adversarial Defense Specification

## 1. Problem Solved
Byzantine Robustness protects federated consortium models against malicious, compromised, or faulty banking nodes attempting to:
1. **Model Poisoning**: Prevent global model convergence by submitting catastrophic gradient updates.
2. **Backdoor Injection**: Embed subtle triggers allowing specific fraud syndicates to bypass risk scoring while maintaining high validation accuracy on normal transactions.
3. **Sign Inversion**: Invert update signs to negate fraud pattern learning.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/domain/byzantine_defense.py`](file:///backend/app/domain/byzantine_defense.py) and [`backend/app/domain/spectral_defense.py`](file:///backend/app/domain/spectral_defense.py)

### 2.1 Coordinate-wise Trimmed Mean
For each coordinate $j \in \{1, \dots, d\}$, the coordinator sorts the client updates $\{ \Delta w_{1,j}, \dots, \Delta w_{K,j} \}$, trims the smallest $\beta$ and largest $\beta$ fractions (where $\beta < 0.5$), and computes the mean of the remaining updates:

$$(\Delta w_{\mathrm{TM}})_j = \frac{1}{K - 2\lfloor \beta K \rfloor} \sum_{i=\lfloor \beta K \rfloor + 1}^{K - \lfloor \beta K \rfloor} \Delta w_{(i), j}$$

### 2.2 Krum & Multi-Krum (Blanchard et al., 2017)
Krum identifies the single client update $\Delta w_i$ that is closest to its $K - f - 2$ nearest neighbors, where $f$ is the maximum number of tolerated Byzantine nodes:

$$i^* = \arg\min_{i} \sum_{j \in \mathcal{N}_i} \|\Delta w_i - \Delta w_j\|_2^2$$

where $\mathcal{N}_i$ contains the $K - f - 2$ closest updates to $\Delta w_i$ in Euclidean distance.
**Multi-Krum** averages the top $m$ candidates satisfying this criterion.

### 2.3 Bulyan (Guerraoui et al., 2018)
Bulyan combines Krum selection with coordinate-wise trimmed mean:
1. Iteratively applies Krum to select a subset $\mathcal{C}$ of size $\theta = K - 2f$ candidates.
2. Computes the coordinate-wise trimmed mean on $\mathcal{C}$, trimming the highest and lowest $\gamma$ values per coordinate.

### 2.4 Spectral SVD Defense
Computes singular value decomposition on the centered client update matrix:

$$\mathbf{M} = [\Delta w_1 - \bar{w}, \dots, \Delta w_K - \bar{w}]^T = \mathbf{U} \mathbf{\Sigma} \mathbf{V}^T$$

Updates with projection scores exceeding $3\sigma$ along the principal singular vector are quarantined as poisoned backdoor candidates.

---

## 3. Theoretical Guarantees & Breakdown Points
- **Krum**: Guarantees non-divergence if $f < \frac{K - 2}{2}$ (tolerance up to $\approx 50\%$ malicious clients).
- **Bulyan**: Requires stricter quorum $K \ge 4f + 3$, but guarantees bounded gradient bias even in high-dimensional spaces where Krum alone can be manipulated.

---

## 4. Operational Limitations
- **Pairwise Distance Computation**: Krum requires $O(K^2 \cdot d)$ Euclidean distance calculations. For massive models, coordinate-wise trimmed mean is computationally preferred.
- **Benign Heterogeneity Penalty**: Under severe Non-IID skew ($\alpha \le 0.1$), honest clients with specialized regional data may appear as outliers and be trimmed.

---

## 5. Test Suite Verification
- **FL Engine Robust Aggregation Tests**: [`backend/tests/unit/test_fl_engine.py`](file:///backend/tests/unit/test_fl_engine.py)
- **Unit Tests**: [`backend/tests/unit/test_byzantine_defense_branches.py`](file:///backend/tests/unit/test_byzantine_defense_branches.py)
- **Spectral Tests**: [`backend/tests/unit/test_spectral_defense.py`](file:///backend/tests/unit/test_spectral_defense.py)
- **Benchmark Runner**: [`benchmarks/runners/run_byzantine_benchmark.py`](file:///benchmarks/runners/run_byzantine_benchmark.py)
- **Benchmark Output**: [`benchmarks/results/raw/byzantine_benchmark_sign_inversion.json`](file:///benchmarks/results/raw/byzantine_benchmark_sign_inversion.json)
