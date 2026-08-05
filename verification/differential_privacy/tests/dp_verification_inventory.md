# Scientific Verification Inventory — Differential Privacy Subsystem

This document presents a complete scientific audit and verification inventory of the Differential Privacy (DP), Private Set Intersection (PETs), and Privacy Audit subsystem within the privacy-preserving cross-bank fraud detection platform.

---

## 1. Inventory Overview

* **Audited Subsystems:** 18 privacy mechanisms, noise generators, sensitivity clippers, budget accountants, leakage evaluators, and cryptographic PET utilities.
* **Primary Implementation Files:**
  * `backend/app/application/services/privacy_service.py`
  * `backend/app/application/services/privacy_audit_service.py`
  * `backend/app/application/services/psi_service.py`
  * `backend/app/application/services/kms_service.py`
  * `backend/app/domain/security_evaluator.py`
  * `backend/app/domain/label_privacy_guard.py`
  * `backend/app/domain/fuzzy_psi.py`
  * `backend/app/domain/value_objects_phase2.py`

---

## 2. Comprehensive Subsystem & Mathematical Inventory

### Component 1: Gaussian Differential Privacy Noise Mechanism
* **Purpose:** Injects calibrated zero-mean Gaussian noise into global/local model parameter updates to guarantee $(\epsilon, \delta)$-Differential Privacy (Dwork & Roth, 2014).
* **Mathematical Formulation:**
  $$\sigma = \frac{\Delta f \sqrt{2 \ln(1.25/\delta)}}{\epsilon}, \quad \tilde{W} = W + \mathcal{N}(\mathbf{0}, \sigma^2 \mathbf{I})$$
* **Privacy Claim:** Guarantees $(\epsilon, \delta)$-DP under bounded L2 global sensitivity $\Delta f \le C$.
* **Expected Invariant:** Noise std-dev $\sigma > 0$; expected noise mean $\mathbb{E}[\tilde{W} - W] = \mathbf{0}$; variance $\operatorname{Var}(\tilde{W} - W) = \sigma^2 \mathbf{I}$.
* **Possible Implementation Risks:** Division by zero if $\epsilon \le 0$; float overflow if $\delta \ge 1.25$; PRNG seed predictability if non-cryptographic RNG is used.
* **Edge Cases:** $\epsilon \to 0^+$ ($\sigma \to \infty$); $\delta \to 1.25$ ($\sigma \to 0$).
* **Scientific Claim:** Implements exact analytical Gaussian mechanism noise scale calibration.
* **Appropriate Verification Methodology:** Reference contract test against analytical formula & Monte Carlo Kolmogorov-Smirnov test ($N = 1,000,000$).

---

### Component 2: L2 Sensitivity Update Clipper
* **Purpose:** Clips model parameter update deltas to a maximum L2 norm threshold $C$ to bound global sensitivity $\Delta f \le C$.
* **Mathematical Formulation:**
  $$\Delta W_{\text{clipped}} = \Delta W \cdot \min\left(1, \frac{C}{\|\Delta W\|_2}\right)$$
* **Privacy Claim:** Guarantees that the L2 norm of any output update delta does not exceed $C$.
* **Expected Invariant:** $\|\Delta W_{\text{clipped}}\|_2 \le C$; cosine similarity $\cos(\Delta W, \Delta W_{\text{clipped}}) = 1.0$ (directional invariance).
* **Possible Implementation Risks:** Division by zero if $\|\Delta W\|_2 = 0$; NaN propagation if input array contains NaNs.
* **Edge Cases:** $\|\Delta W\|_2 \ll C$ (unclipped identity); $\|\Delta W\|_2 \gg C$ (heavy norm reduction); $\|\Delta W\|_2 = 0$.
* **Scientific Claim:** Strict vector norm projection preserving gradient direction.
* **Appropriate Verification Methodology:** Property-based testing (Hypothesis) & extreme float failure injection.

---

### Component 3: Linear Privacy Budget Composition Accountant
* **Purpose:** Accumulates total privacy loss ($\epsilon_{\text{total}}, \delta_{\text{total}}$) across sequential training rounds using linear composition.
* **Mathematical Formulation:**
  $$\epsilon_{\text{total}} = \sum_{t=1}^T \epsilon_t, \quad \delta_{\text{total}} = \sum_{t=1}^T \delta_t = T \cdot \delta$$
* **Privacy Claim:** Maintains a valid mathematical upper bound on cumulative privacy budget expenditure.
* **Expected Invariant:** $\epsilon_{\text{total}}$ and $\delta_{\text{total}}$ are monotonically non-decreasing functions of round count $T$.
* **Possible Implementation Risks:** Floating point rounding drift over long training runs ($T \gg 10^3$).
* **Edge Cases:** Zero spent budget $\epsilon_t = 0$; negative spending inputs.
* **Scientific Claim:** Basic linear composition upper-bound accountant.
* **Appropriate Verification Methodology:** Numerical contract test & property-based monotonicity checks.

---

### Component 4: Opacus Rényi DP Budget Recorder
* **Purpose:** Records external PyTorch Opacus RDP (Rényi Differential Privacy) accountant values into the global platform budget tracker.
* **Mathematical Formulation:**
  $$\epsilon_{\text{RDP}}(\alpha) \le \epsilon_{\text{target}}, \quad \epsilon(\delta) = \min_{\alpha > 1} \left( \epsilon_{\text{RDP}}(\alpha) + \frac{\ln(1/\delta)}{\alpha - 1} \right)$$
* **Privacy Claim:** Accurate pass-through logging of tight moments-accountant privacy guarantees.
* **Expected Invariant:** Logged RDP epsilon matches PyTorch Opacus engine calculation.
* **Possible Implementation Risks:** Discrepancy between internal linear accountant and logged RDP value.
* **Edge Cases:** Uninitialized RDP accountant; negative epsilon inputs.
* **Scientific Claim:** Valid RDP budget tracking interface.
* **Appropriate Verification Methodology:** Unit integration test against Opacus accountant objects.

---

### Component 5: Privacy Budget Exhaustion Guard
* **Purpose:** Blocks execution and raises a security exception when cumulative privacy expenditure exceeds configured limits ($\epsilon_{\text{max}}, \delta_{\text{max}}$).
* **Mathematical Formulation:**
  $$\text{If } \epsilon_{\text{total}} + \epsilon_{\text{step}} > \epsilon_{\text{max}} \implies \text{Raise PrivacyBudgetExceededError}$$
* **Privacy Claim:** Enforces strict privacy budget enforcement preventing privacy catastrophic failure.
* **Expected Invariant:** No privacy-sensitive model computation executes once $\epsilon_{\text{total}} > \epsilon_{\text{max}}$.
* **Possible Implementation Risks:** Race conditions in concurrent async requests bypassing budget check.
* **Edge Cases:** Exact boundary condition $\epsilon_{\text{total}} + \epsilon_{\text{step}} = \epsilon_{\text{max}}$.
* **Scientific Claim:** Hard privacy threshold enforcement guard.
* **Appropriate Verification Methodology:** Boundary testing & concurrency stress testing.

---

### Component 6: Link Reconstruction Attack Audit
* **Purpose:** Audits graph edge leakage vulnerability by measuring cosine similarity ROC-AUC on GNN node embeddings.
* **Mathematical Formulation:**
  $$\text{Score}(u, v) = \frac{\mathbf{z}_u^T \mathbf{z}_v}{\|\mathbf{z}_u\| \|\mathbf{z}_v\|}, \quad \text{AUC} = \int_{0}^1 \text{TPR}(\text{FPR}^{-1}(t)) dt$$
* **Privacy Claim:** Quantifies empirical graph topology reconstructability from node representations.
* **Expected Invariant:** Random embeddings yield $\text{AUC} \approx 0.5$; identical linked nodes yield $\text{AUC} \to 1.0$.
* **Possible Implementation Risks:** Zero division in cosine similarity when node embedding norm is zero.
* **Edge Cases:** Single node graph, disconnected components, zero embedding vectors.
* **Scientific Claim:** Empirical ROC-AUC link reconstruction risk proxy.
* **Appropriate Verification Methodology:** Empirical graph baseline contract test.

---

### Component 7: Membership Inference Loss Threshold Audit
* **Purpose:** Audits Membership Inference Attack (MIA) vulnerability using training vs test prediction loss thresholding (Yeom et al., 2018).
* **Mathematical Formulation:**
  $$\mathcal{L}_{\text{BCE}}(y, \hat{y}) = -[y \ln \hat{y} + (1-y) \ln(1-\hat{y})], \quad \text{Guess Member if } \mathcal{L} < \tau_{\text{median}}$$
* **Privacy Claim:** Evaluates empirical membership advantage $\text{Adv} = 2 |\text{Accuracy} - 0.5|$.
* **Expected Invariant:** Overfitted models yield $\text{Adv} \gg 0$; DP-protected models yield $\text{Adv} \approx 0$.
* **Possible Implementation Risks:** Log of zero ($\ln(0)$) in BCE loss computation.
* **Edge Cases:** Zero training loss, empty loss lists, identical train/test loss distributions.
* **Scientific Claim:** Standard loss-threshold Membership Inference attack benchmark.
* **Appropriate Verification Methodology:** Synthetic overfitted vs DP model audit evaluation.

---

### Component 8: Empirical Model Inversion Risk Audit
* **Purpose:** Measures feature reconstruction vulnerability and gradient norm variance across client updates.
* **Mathematical Formulation:**
  $$\text{CV} = \frac{\sigma_{\|\nabla W\|}}{\mu_{\|\nabla W\|} + \epsilon}, \quad \text{Risk Score} = \min\left(1.0, \text{CV}\right)$$
* **Privacy Claim:** Quantifies feature distinguishability from gradient update magnitudes.
* **Expected Invariant:** High gradient norm variance $\implies$ high reconstruction risk tier.
* **Possible Implementation Risks:** Division by zero if mean gradient norm is zero.
* **Edge Cases:** Homogeneous zero gradients, single gradient norm input.
* **Scientific Claim:** Empirical gradient norm variance feature inversion risk metric.
* **Appropriate Verification Methodology:** Gradient norm variance contract testing.

---

### Component 9: DLG Gradient Leakage Audit
* **Purpose:** Evaluates feature reconstructability by measuring Pearson correlation between raw and received gradients.
* **Mathematical Formulation:**
  $$r = \frac{\sum (g_i - \bar{g})(\hat{g}_i - \bar{\hat{g}})}{\sqrt{\sum (g_i - \bar{g})^2 \sum (\hat{g}_i - \bar{\hat{g}})^2}}$$
* **Privacy Claim:** Quantifies linear gradient leakage under raw vs noised/masked gradient transmission.
* **Expected Invariant:** $r \approx 1.0$ for plaintext transmission; $r \approx 0.0$ under DP noise or SecAgg masks.
* **Possible Implementation Risks:** Zero denominator when gradient vectors are constant.
* **Edge Cases:** Constant gradient vectors ($g_i = c$), empty gradient arrays.
* **Scientific Claim:** Pearson correlation gradient reconstruction leakage metric.
* **Appropriate Verification Methodology:** Reference contract test against SciPy Pearson implementation.

---

### Component 10: Empirical Shadow Model MIA Evaluator
* **Purpose:** Evaluates Membership Inference vulnerability across unprotected vs DP-protected models using shadow loss threshold classification.
* **Mathematical Formulation:**
  $$\text{Predict Member if } \mathcal{L}(y, f(x)) < \operatorname{median}(\mathcal{L}), \quad \text{Advantage} = 2 \cdot |\text{Accuracy} - 0.5|$$
* **Privacy Claim:** Un-clipped empirical MIA accuracy and attack advantage calculation.
* **Expected Invariant:** DP protection ($\epsilon = 1.0$) degrades attack advantage toward zero ($\text{Adv} < 0.05$).
* **Possible Implementation Risks:** Extreme probability inputs ($0.0$ or $1.0$) causing `log(0)` invalid math.
* **Edge Cases:** Single sample input, perfectly separated probabilities.
* **Scientific Claim:** Empirical shadow model loss threshold MIA attack evaluation.
* **Appropriate Verification Methodology:** Empirical shadow attack validation test.

---

### Component 11: DLG Feature Reconstruction Evaluator
* **Purpose:** Simulates feature reconstruction optimization matching dummy gradients to target gradients.
* **Mathematical Formulation:**
  $$\min_{\hat{x}} \|\nabla W(x) - \nabla W(\hat{x})\|_2^2, \quad \text{MSE} = \frac{1}{d}\|\mathbf{x} - \hat{\mathbf{x}}\|_2^2$$
* **Privacy Claim:** Un-clipped empirical Pearson correlation $r$ and L2 MSE feature reconstruction evaluation.
* **Expected Invariant:** SecAgg and DP noise reduce Pearson correlation to near zero ($r < 0.10$).
* **Possible Implementation Risks:** Zero variance in dummy features causing zero Pearson denominator.
* **Edge Cases:** Zero feature dimension $d=0$, constant feature vectors.
* **Scientific Claim:** Empirical gradient matching DLG reconstruction attack evaluation.
* **Appropriate Verification Methodology:** Empirical gradient matching contract test.

---

### Component 12: PII Identifier Guard
* **Purpose:** Scans input metadata payloads for raw PII patterns (IBAN, SSN, email) and enforces valid DP epsilon ranges.
* **Mathematical Formulation:**
  $$\text{Regex Scan}(S) \cap \{\text{IBAN}, \text{SSN}, \text{Email}\} \neq \emptyset \implies \text{Raise LabelPrivacyViolationError}$$
* **Privacy Claim:** Prevents accidental plaintext PII leakage in model metadata or unstructured payloads.
* **Expected Invariant:** Zero raw PII strings pass into training or aggregation workflows.
* **Possible Implementation Risks:** Regex bypass via whitespace or unicode obfuscation.
* **Edge Cases:** Obfuscated email addresses ("user [at] bank [dot] com"), non-standard IBAN formats.
* **Scientific Claim:** Deterministic PII payload sanitizer.
* **Appropriate Verification Methodology:** Adversarial PII string fuzzer.

---

### Component 13: Diffie-Hellman 2048-bit Private Set Intersection (DH-PSI)
* **Purpose:** Performs exact cryptographic private set intersection between bank entity sets without disclosing non-overlapping entities (De Cristofaro & Tsudik, 2010).
* **Mathematical Formulation:**
  $$\text{Bank A: } H(x)^{k_A} \pmod p, \quad \text{Bank B: } (H(x)^{k_A})^{k_B} \pmod p = H(x)^{k_A k_B} \pmod p$$
  NIST-compliant 2048-bit MODP prime (RFC 3526 Group 14).
* **Privacy Claim:** Zero-knowledge set intersection; no information leaked regarding non-matching entities.
* **Expected Invariant:** $H(x)^{k_A k_B} = H(x)^{k_B k_A} \pmod p$ (commutative group exponentiation).
* **Possible Implementation Risks:** Exponentiation side-channel timing leaks; small subgroup attacks.
* **Edge Cases:** Empty entity sets, 100% disjoint sets, 100% identical sets.
* **Scientific Claim:** NIST SP 800-131A compliant 2048-bit commutative DH-PSI protocol.
* **Appropriate Verification Methodology:** Cryptographic commutativity unit test & byte exchange validation.

---

### Component 14: Multi-Attribute Attribute-Overlap Fuzzy PSI
* **Purpose:** Enables entity resolution over noisy or incomplete entity fields (phone, email, device ID, birthdate, surname).
* **Mathematical Formulation:**
  $$\text{Match Ratio} = \frac{|\text{Matched Attrs}|}{K} \ge \theta_{\text{threshold}}$$
* **Privacy Claim:** Bounded entity matching under attribute noise without exposing raw attributes.
* **Expected Invariant:** Match ratio strictly in $[0, 1]$; threshold $\theta = 0.6 \implies \ge 3/5$ matching fields.
* **Possible Implementation Risks:** Over-matching on common default attributes (e.g. empty strings).
* **Edge Cases:** All attributes empty, missing optional fields.
* **Scientific Claim:** Multi-attribute overlap fuzzy entity resolution.
* **Appropriate Verification Methodology:** Attribute permutation & fuzzy overlap contract test.

---

### Component 15: MinHash LSH Signature Matcher
* **Purpose:** Computes Locality-Sensitive Hashing (LSH) MinHash signatures for Jaccard similarity estimation of string attributes (Broder, 1997).
* **Mathematical Formulation:**
  $$h_{\min}(S) = \min_{s \in S} h_k(s), \quad \mathbb{P}(h_{\min}(S_A) = h_{\min}(S_B)) = J(S_A, S_B)$$
  16 bands $\times$ 4 rows = 64 min-hash permutation functions.
* **Privacy Claim:** Unbiased Jaccard similarity estimation without transmitting raw text.
* **Expected Invariant:** $\mathbb{E}[\hat{J}] = J_{\text{true}}$; LSH collision probability $P_{\text{match}} = 1 - (1 - s^r)^b$.
* **Possible Implementation Risks:** Non-uniform hash distribution in linear congruential hash functions.
* **Edge Cases:** Single character strings, empty text sets.
* **Scientific Claim:** MinHash LSH Jaccard similarity estimator.
* **Appropriate Verification Methodology:** Monte Carlo Jaccard estimation validation ($N = 10,000$).

---

### Component 16: Privacy-Preserving Identifier HMAC Hasher
* **Purpose:** Computes deterministic, tenant-salted HMAC-SHA256 entity hashes for cross-bank matching.
* **Mathematical Formulation:**
  $$\text{ID} = \text{HMAC-SHA256}(k_{\text{tenant}}, \text{type} \parallel x)_{\text{hex}}[:32]$$
  128-bit truncated output (32 hex characters).
* **Privacy Claim:** Irreversible, collision-resistant entity identification ($2^{64}$ search space bound).
* **Expected Invariant:** Deterministic ($x_1 = x_2 \implies \text{ID}_1 = \text{ID}_2$); isolated ($k_A \neq k_B \implies \text{ID}_A \neq \text{ID}_B$).
* **Possible Implementation Risks:** Key leakage if tenant keys are hardcoded.
* **Edge Cases:** Empty string identifier, special character PII.
* **Scientific Claim:** 128-bit collision-resistant HMAC entity identifier.
* **Appropriate Verification Methodology:** Property-based determinism & isolation testing.

---

### Component 17: KMS Per-Tenant Key Storage Vault
* **Purpose:** Manages isolated per-bank cryptographic keys (HMAC keys and DH exponents) on disk.
* **Mathematical Formulation:**
  $$\text{Vault}(B_i) = \{k_{\text{hmac}}^{(i)}, k_{\text{dh}}^{(i)}\} \quad \text{where } \text{Path} = \text{storage}/B_i/\text{kms}/keys.json$$
* **Privacy Claim:** Strict per-tenant key separation preventing inter-bank key leakage.
* **Expected Invariant:** Bank A cannot read or derive Bank B's private exponent $k_{\text{dh}}^{(B)}$.
* **Possible Implementation Risks:** Unencrypted storage on disk (software simulation); file permissions.
* **Edge Cases:** Missing key directory, concurrent multi-tenant key generation.
* **Scientific Claim:** Per-tenant isolated cryptographic key vault.
* **Appropriate Verification Methodology:** Multi-tenant key isolation unit test.

---

### Component 18: DP Simulation Configuration Dataclass Container
* **Purpose:** Encapsulates and validates all Differential Privacy simulation parameters.
* **Mathematical Formulation:**
  $$\mathcal{C}_{\text{DP}} = \{ \epsilon \in (0, \infty), \, \delta \in (0, 1), \, C \in (0, \infty), \, \text{noise\_type} \in \{\text{Gaussian}, \text{Laplace}\} \}$$
* **Privacy Claim:** Structurally validated parameter container for DP execution.
* **Expected Invariant:** Range bounds $\epsilon > 0$, $0 < \delta < 1$, $C > 0$ strictly enforced.
* **Possible Implementation Risks:** Mutable default arguments in dataclass fields.
* **Edge Cases:** Invalid hyperparameter strings.
* **Scientific Claim:** Type-safe DP simulation configuration object.
* **Appropriate Verification Methodology:** Unit validation testing.

---

*This document completes the scientific verification inventory for the Differential Privacy subsystem.*
