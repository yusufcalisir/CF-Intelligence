# Canonical Differential Privacy Benchmark Audit Dossier (Opacus DP-SGD)

**Verification Engine:** PyTorch Opacus `PrivacyEngine(accountant='prv')`  
**Privacy Accountant:** PRVAccountant (Privacy Random Variables, Gopi et al. 2021)  
**Sweep Type:** Fixed-noise-multiplier (σ ∈ {3.0, 2.0, 1.0, 0.5, 0.0}) — NOT target-ε calibration  
**Dataset:** Synthetic Banking (N=20,000, 15 features, prevalence=2.0%)  
**Test Support:** 84 fraud transactions out of 4,000 test samples  
**Seeds Evaluated:** [42, 123, 456]  
**Security Parameter:** $\delta = 1e-05$ ($< 1/N_{\text{train}}$)  
**Reported ±:** Sample standard deviation across training seeds (ddof=1).  
Measures training stochasticity only. Dataset realization is fixed (data_seed=42).  

## 1. Canonical Privacy-Utility Frontier

| Noise Multiplier (σ) | Accounted Epsilon (ε) | Test PR-AUC (mean ± sample std) | Test ROC-AUC | Relative Utility Loss | Regime |
| :---: | :---: | :---: | :---: | :---: | :---|
| **σ = 3.0** | ε = 0.3497 | **0.3465 ± 0.1580** | 0.8720 ± 0.0551 | -61.4% | High Privacy Regime |
| **σ = 2.0** | ε = 0.5725 | **0.4710 ± 0.1752** | 0.8990 ± 0.0431 | -47.5% | Strong Privacy |
| **σ = 1.0** | ε = 1.7744 | **0.7088 ± 0.1155** | 0.9514 ± 0.0184 | -20.9% | Balanced Tradeoff |
| **σ = 0.5** | ε = 12.1989 | **0.8457 ± 0.0429** | 0.9777 ± 0.0047 | -5.7% | Weak Privacy |
| **σ = 0.0** | infinity (non-private) | **0.8965 ± 0.0078** | 0.9872 ± 0.0050 | - | Non-Private Baseline |

## 2. Per-Seed Verification Breakdown

| σ | Seed 42 PR-AUC | Seed 123 PR-AUC | Seed 456 PR-AUC | Min | Max | Std |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| σ = 3.0 | 0.3134 | 0.5185 | 0.2077 | 0.2077 | 0.5185 | 0.1580 |
| σ = 2.0 | 0.4169 | 0.6668 | 0.3292 | 0.3292 | 0.6668 | 0.1752 |
| σ = 1.0 | 0.7321 | 0.8109 | 0.5835 | 0.5835 | 0.8109 | 0.1155 |
| σ = 0.5 | 0.8795 | 0.8600 | 0.7975 | 0.7975 | 0.8795 | 0.0429 |
| σ = 0.0 | 0.8987 | 0.9031 | 0.8879 | 0.8879 | 0.9031 | 0.0078 |

## 3. Methodological Invariants Verified

- **Genuine Per-Sample DP-SGD**: Evaluated through PyTorch Opacus hooks with gradient clipping norm $C=1.0$.
- **Explicit PRVAccountant**: `PrivacyEngine(accountant='prv')` pins accounting method; no implicit default dependency.
- **Fixed-Sigma Sweep**: Each σ value is passed as `noise_multiplier=σ` to `make_private()`. This is NOT target-ε calibration via `make_private_with_epsilon()`.
- **Matching Non-Private Baseline**: Unperturbed baseline uses identical architecture, optimizer, batch size, and epochs.
- **Live Accountant Tracking**: Reported ε bounds are emitted from the same `PrivacyEngine` instance that observed all 315 DPOptimizer steps (PRVAccountant, Gopi et al. 2021).
- **Sample Std (ddof=1)**: Reported ± is the unbiased sample standard deviation across n=3 seeds, not population std.
- **Zero Static Fixtures**: All reported points are calculated dynamically from model execution.

## 4. Statistical Limitations

- **3 training seeds only**: std captures training stochasticity, NOT dataset-sampling uncertainty.
- **Fixed dataset realization**: All runs use the same synthetic dataset generated with `data_seed=42`.
- **84 test fraud examples**: Adequate for relative trend analysis; insufficient for high-precision recall-at-threshold claims.
- **Synthetic dataset**: Results reflect a controlled synthetic distribution; real-world performance may differ.

## 5. Legacy Prototype Archival Reference

The legacy 10-feature algebraic centroid prototype (commit `f0705141`) achieved `PR-AUC 0.6272` (baseline) and `0.1963` (at σ=3.0). These values are preserved solely as historical artifacts and are not part of the active canonical neural benchmark.