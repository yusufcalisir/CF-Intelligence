# CF-Intelligence — Scientific Revalidation Protocol V1.0

## Clean Reproduction of Historically Affected Benchmark Evidence

---

### 1. Mission and Context
Following the formal closure of the semantic and correctness remediation phase (culminating in commit `a0bb088febdc3fa3da16a620b281946ac8694e31`), this protocol establishes an immutable preregistered scientific methodology for regenerating valid, defensible evidence for six historical revalidation units:

- **RU-CC-01**: European Credit Card (`run_creditcard_benchmark.py` — `FL-001` + `DATA-002`)
- **RU-PS-01**: PaySim (`run_paysim_canonical_benchmark.py` — `FL-001`)
- **RU-EL-01**: Elliptic Bitcoin GraphSAGE (`train_graphsage.py` — `GRAPH-LEAK-01` + `DATA-007`)
- **RU-IE-01**: IEEE-CIS (`run_ieee_benchmark.py` — `FL-001`)
- **RU-AML-01**: IBM AMLSim (`evaluate_patterns.py` — `DATA-006`)
- **RU-MS-01**: Multi-Seed Statistical Runner (`multi_seed_runner.py` — `FL-001` + `CRYPTO-001`)

---

### 2. Protocol Metadata
- **Protocol Name**: `CF_INTELLIGENCE_SCIENTIFIC_REVALIDATION_V1`
- **Protocol Version**: `1.0`
- **Created At (UTC)**: `2026-10-06T15:19:30Z`
- **Source Closure Commit**: `a0bb088febdc3fa3da16a620b281946ac8694e31`
- **Pre-Preregistration HEAD**: `7cc1f003177a95004ce60d86305065e73b722c7a`
- **Protocol Content Commit**: `d5b3a5883fe822304ab72181348bc42fd74560d4`
- **Repository Worktree**: Clean

---

### 3. Absolute Scientific Integrity Rules
1. **No Metric Fabrication**: Zero mock results, placeholder metrics, precomputed metrics, or hardcoded numbers.
2. **No Silent Fallback**: If a dataset is missing or its hash does not match, the experiment fails closed (`BLOCKED_DATASET_UNAVAILABLE` or `BLOCKED_DATASET_IDENTITY_MISMATCH`). It must never silently synthesize data.
3. **Scientific Origin Truthfulness**:
   - `EMPIRICAL`: Real-world observation data (CreditCard, Elliptic, IEEE-CIS).
   - `SIMULATED`: Agent-based and financial simulator data (PaySim, AMLSim).
   - `SYNTHETIC`: Controlled mathematical generator data (In Silico Gaussian).
4. **Final External Evaluation Firewall**: The final unseen evaluation dataset is completely firewalled. No searching, downloading, inspecting, or tuning against unseen candidates.
5. **No Hyperparameter Optimization / HARKing**: Revalidation executes frozen hyperparameters. Sub-optimal metrics are reported truthfully as results, not optimized away.
6. **Historical Immutability**: Historical raw benchmark artifacts in `benchmarks/results/raw/` remain strictly untouched and auditable.

---

### 4. Canonical Defect Mitigation Invariants
- **FL-001 (Zero FL Weights)**: Aggregation denominators enforce `sum(n_k) > 0` and reject empty client update lists. Missing or disconnected clients are properly excluded from weight sums.
- **DATA-002 (CreditCard Split Discrepancy)**: Strict 80% train / 20% holdout test split (`seed=42`) with extreme-skew client partitioning.
- **GRAPH-LEAK-01 (Feature 7 Label Leakage)**: Node feature index 7 is confirmed clean in raw Elliptic features and masked to 0.0 in graph extractors, eliminating label proxy leakage.
- **DATA-006 (AMLSim NaN Boolean Coercion)**: Missing or NaN labels fail closed. `astype(bool)` coercion is banned.
- **DATA-007 (Elliptic Label Permissiveness)**: Class vocabulary strictly enforced as `{'1', '2', 'unknown'}`. Unrecognized strings raise errors.
- **CRYPTO-001 (FHE Emulation Truthfulness)**: Plaintext software simulation is explicitly labeled `PLAINTEXT_EMULATION_OF_FHE_WORKFLOW` and never claimed as hardware TEE or ciphertext.

---

### 5. Execution Sequence
The recommended execution order optimizes failure detection and compute feasibility:
1. `RU-AML-01`: Evaluation-only recomputation; zero retraining; fastest verification of label truthfulness.
2. `RU-EL-01`: Full graph evaluation (203k nodes) with verified leakage guards and class vocabulary checks.
3. `RU-CC-01`: CreditCard FL benchmark (284k transactions) testing extreme imbalance under corrected 80/20 split and FedAvg consensus.
4. `RU-PS-01`: PaySim canonical benchmark (systematic ~636k sample) across seeds [42, 123, 456].
5. `RU-IE-01`: Full IEEE-CIS benchmark on physical 590k transactions.
6. `RU-MS-01`: Multi-seed statistical evaluation across 5 seeds [42, 123, 456, 789, 1024].
