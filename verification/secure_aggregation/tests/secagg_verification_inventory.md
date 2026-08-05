# Scientific Verification Inventory — Secure Aggregation Subsystem

This document presents a complete scientific audit and verification inventory of the Secure Aggregation (SecAgg), Trusted Execution Environment (TEE), and Cryptographic Key Management (KMS) subsystem within the privacy-preserving cross-bank fraud detection platform.

---

## 1. Inventory Overview

* **Audited Subsystems:** 12 cryptographic algorithms, zero-sum noise generators, AES-256-GCM data sealing drivers, HKDF key derivation functions, and pipeline safety guards.
* **Primary Implementation Files:**
  * `backend/app/application/services/fl_engine.py` (`apply_secure_aggregation_masks`)
  * `backend/app/infrastructure/security/tee_driver.py` (`TEEDriver`)
  * `backend/app/application/services/kms_service.py` (`KMSService`)
  * `backend/app/domain/security_evaluator.py` (`DLGEvaluator`)
  * `backend/app/application/services/simulation_service.py` (`InvalidPipelineConfigurationError`)

---

## 2. Comprehensive Subsystem & Cryptographic Inventory

### Component 1: Unweighted Zero-Sum Masking Algorithm
* **Purpose:** Obscures individual client model parameter updates $w_i$ using zero-sum random noise vectors that cancel out identically upon unweighted summation across $n$ clients (Bonawitz et al., 2017).
* **Mathematical Formulation:**
  $$m_i \sim \mathcal{N}(\mathbf{0}, \mathbf{I}_d) \text{ for } i = 1, \dots, n-1, \quad m_n = -\sum_{i=1}^{n-1} m_i \implies \sum_{i=1}^n m_i = \mathbf{0}$$
* **Security Claim:** Individual client parameters $\tilde{w}_i = w_i + m_i$ are obscured by zero-mean Gaussian noise vectors $\|m_i\|_2 > 0$, while the global aggregate $\frac{1}{n}\sum \tilde{w}_i = \frac{1}{n}\sum w_i$ exactly matches plaintext FedAvg.
* **Expected Invariant:** Zero-sum cancellation residual $\|\sum_{i=1}^n m_i\|_2 \le \epsilon_{\text{mach}} \cdot \sqrt{n d}$.
* **Possible Implementation Risks:** Centralized mask generation on the aggregator server rather than pairwise client-side Diffie-Hellman key exchange; single-client dropout corrupts unmasked summation.
* **Edge Cases:** Single client $n=1 \implies m_1 = \mathbf{0}$; $d=0$ empty parameter vector; $n \to \infty$ floating-point accumulation error.
* **Scientific Claim:** Exact zero-sum noise cancellation under unweighted FedAvg aggregation.
* **Appropriate Verification Methodology:** Pure NumPy numerical reference comparison ($MAE \le 10^{-15}$) & property-based testing (Hypothesis).

---

### Component 2: Weighted Zero-Sum Masking Algorithm
* **Purpose:** Obscures client parameters under sample-proportional weighted FedAvg ($p_i = s_i / \sum s_j$) by enforcing weighted zero-sum noise cancellation.
* **Mathematical Formulation:**
  $$m_i \sim \mathcal{N}(\mathbf{0}, \mathbf{I}_d) \text{ for } i = 1, \dots, n-1, \quad m_n = -\frac{1}{p_n} \sum_{i=1}^{n-1} p_i m_i \implies \sum_{i=1}^n p_i m_i = \mathbf{0}$$
* **Security Claim:** Weighted global aggregate $\sum p_i \tilde{w}_i = \sum p_i w_i + \mathbf{0} = \bar{w}_{\text{weighted}}$ preserves exact weighted FedAvg output.
* **Expected Invariant:** Weighted zero-sum residual $\|\sum_{i=1}^n p_i m_i\|_2 \le \epsilon_{\text{mach}} \cdot \sqrt{n d}$.
* **Possible Implementation Risks:** Division by zero if $p_n = 0$ (client with 0 training samples); numerical overflow if $p_n \to 0^+$.
* **Edge Cases:** Extreme sample imbalance ($p_n \approx 10^{-6}$), zero total samples $\sum s_i = 0$.
* **Scientific Claim:** Exact weighted zero-sum noise cancellation under sample-proportional FedAvg.
* **Appropriate Verification Methodology:** Reference contract test against analytical weighted average & extreme imbalance ratio fuzzing.

---

### Component 3: KMS 256-Bit Master Mask Seed Generator
* **Purpose:** Generates cryptographically secure 256-bit master seeds for secure aggregation PRNG initialization.
* **Mathematical Formulation:**
  $$\text{Seed} = \text{CSPRNG}(256 \text{ bits}) \in \{0, 1\}^{256}$$
* **Security Claim:** High entropy ($2^{256}$ search space), non-predictable master seeds generated via OS entropy pools (`secrets.token_hex(32)`).
* **Expected Invariant:** Seed entropy $\ge 256$ bits; unique across different bank tenants.
* **Possible Implementation Risks:** Insecure PRNG initialization if `random.random()` is used instead of `secrets`.
* **Edge Cases:** Disk read/write failures on key vault initialization.
* **Scientific Claim:** CSPRNG 256-bit master seed generation.
* **Appropriate Verification Methodology:** Entropy inspection & unit test validation.

---

### Component 4: KMS HKDF-SHA256 Per-Round Key Derivation Function
* **Purpose:** Derives unique round-specific mask keys $K_t$ from the tenant master seed using HKDF-SHA256 (RFC 5869), eliminating cross-round update differencing attacks.
* **Mathematical Formulation:**
  $$K_t = \text{HKDF-SHA256}(\text{master\_seed}, \text{info} = \text{"secagg\_round\_"} \parallel t, \text{length} = 32)$$
* **Security Claim:** Cryptographically isolates round keys ($K_{t_1} \neq K_{t_2}$ for $t_1 \neq t_2$), ensuring static key storage in `keys.json` cannot be exploited to subtract masks across sequential training rounds.
* **Expected Invariant:** $K_{t_1} \neq K_{t_2}$ for any $t_1 \neq t_2$; $K_t$ is deterministic for identical $(K_{\text{master}}, t)$.
* **Possible Implementation Risks:** Hardcoded salt or info parameter truncation.
* **Edge Cases:** Round counter overflow ($t > 10^9$).
* **Scientific Claim:** NIST SP 800-56C compliant HKDF-SHA256 per-round key diversification.
* **Appropriate Verification Methodology:** Cryptographic unit test & property-based uniqueness validation.

---

### Component 5: KMS Tenant Master Seed Rotation
* **Purpose:** Enables force-rotation of tenant master seeds and cryptographic parameters stored in local JSON key vaults.
* **Mathematical Formulation:**
  $$\text{Vault}(B_i) \leftarrow \text{Vault}(B_i) \setminus \{k_{\text{old}}\} \cup \{k_{\text{new}}\}$$
* **Security Claim:** Supports key lifecycle management and forward secrecy upon key revocation.
* **Expected Invariant:** Newly generated key is distinct from pre-rotation key ($k_{\text{new}} \neq k_{\text{old}}$).
* **Possible Implementation Risks:** Race conditions during concurrent key rotation and active training round execution.
* **Edge Cases:** Key rotation during active round aggregation.
* **Scientific Claim:** Dynamic tenant key lifecycle management.
* **Appropriate Verification Methodology:** Key rotation unit integration test.

---

### Component 6: TEE Hardware Enclave Measurement Driver
* **Purpose:** Initializes secure isolated hardware memory enclaves (Intel SGX / AWS Nitro Enclaves) and computes cryptographic code layout measurements (`MRENCLAVE`, `MRSIGNER`).
* **Mathematical Formulation:**
  $$\text{MRENCLAVE} = \text{SHA256}(\text{Code}_{\text{layout}} \parallel \text{id}), \quad \text{MRSIGNER} = \text{SHA256}(\text{Signer}_{\text{pubkey}})$$
* **Security Claim:** Verifiable cryptographic measurement identity of code executing inside enclave memory.
* **Expected Invariant:** `MRENCLAVE` and `MRSIGNER` are 64-character SHA-256 hex strings.
* **Possible Implementation Risks:** Simulation driver mock (`time.sleep`) without hardware SGX/Nitro SDK bindings.
* **Edge Cases:** Corrupted simulation ID string.
* **Scientific Claim:** TEE hardware enclave measurement driver.
* **Appropriate Verification Methodology:** Enclave measurement hash verification unit test.

---

### Component 7: TEE Remote Attestation Report Generator
* **Purpose:** Generates a cryptographically signed attestation report verifying enclave measurement integrity for remote aggregators.
* **Mathematical Formulation:**
  $$\text{Sig} = \text{SHA256}(\text{id} \parallel \text{MRENCLAVE} \parallel \text{MRSIGNER} \parallel k_{\text{private}})$$
* **Security Claim:** Proves enclave authenticity and un-tampered code execution state to third parties.
* **Expected Invariant:** `verified == True`; signature matches enclave public key verification.
* **Possible Implementation Risks:** Insecure local SHA-256 signature instead of ECDSA secp256r1 hardware attestation signature.
* **Edge Cases:** Invalid attestation private key string.
* **Scientific Claim:** Remote attestation measurement report generator.
* **Appropriate Verification Methodology:** Attestation report verification unit test.

---

### Component 8: TEE Isolated Memory Secure Aggregator
* **Purpose:** Ingests client weight parameters into secure enclave memory, executes plaintext summation inside hardware isolated boundaries, and outputs global model parameters.
* **Mathematical Formulation:**
  $$\bar{w}_{\text{enclave}} = \sum_{i=1}^n p_i w_i \quad \text{inside isolated enclave RAM}$$
* **Security Claim:** Plaintext parameter updates $w_i$ are never exposed outside hardware enclave memory boundaries.
* **Expected Invariant:** Aggregate output $\bar{w}_{\text{enclave}}$ exactly equals FedAvg weighted sum.
* **Possible Implementation Risks:** Simulation memory copy overhead; memory allocation limits inside enclave.
* **Edge Cases:** Single client $n=1$, zero total samples $\sum s_i = 0$.
* **Scientific Claim:** Hardware isolated TEE secure memory parameter aggregator.
* **Appropriate Verification Methodology:** TEE aggregation unit test & shape validation.

---

### Component 9: TEE AES-256-GCM Data Sealing Encryptor
* **Purpose:** Encrypts sensitive enclave state data before writing to untrusted local storage using NIST SP 800-38D AES-256-GCM authenticated encryption.
* **Mathematical Formulation:**
  $$C, T = \text{AES-256-GCM-Encrypt}(k_{\text{derived}}, \text{IV}_{96}, P, \text{AAD}=\emptyset)$$
* **Security Claim:** Confidentiality and integrity protection for sealed data on disk; 128-bit MAC tag $T$ prevents undetected ciphertext tampering.
* **Expected Invariant:** Output format $\text{IV}_{96} \parallel C \parallel T$; ciphertext length $|C| = |P| + 16$.
* **Possible Implementation Risks:** Nonce reuse across encryption calls (prevented via 96-bit CSPRNG `os.urandom(12)`).
* **Edge Cases:** Empty plaintext $P = \emptyset$; short key length.
* **Scientific Claim:** NIST SP 800-38D AES-256-GCM authenticated data sealing.
* **Appropriate Verification Methodology:** Unit test & ciphertext bit-flipping tampering injection test.

---

### Component 10: TEE AES-256-GCM Data Unsealing Decryptor
* **Purpose:** Authenticates and decrypts sealed storage data into enclave memory, raising an exception if tampered.
* **Mathematical Formulation:**
  $$P = \text{AES-256-GCM-Decrypt}(k_{\text{derived}}, \text{IV}_{96}, C, T) \quad \text{or raise InvalidTag}$$
* **Security Claim:** Guarantees that tampered ciphertext or invalid decryption keys strictly fail authentication.
* **Expected Invariant:** Unsealed plaintext matches original sealed data; tampered bytes raise `ValueError`.
* **Possible Implementation Risks:** Truncated ciphertext payload $< 28$ bytes.
* **Edge Cases:** Bit-flipped ciphertext byte, incorrect decryption key.
* **Scientific Claim:** Authenticated decryption and tamper detection unsealing.
* **Appropriate Verification Methodology:** Bit-flipping failure injection & short payload testing.

---

### Component 11: DLG Gradient Feature Reconstruction Evaluator
* **Purpose:** Evaluates feature reconstructability by computing un-clipped empirical Pearson correlation $r$ and L2 MSE between original and reconstructed feature vectors.
* **Mathematical Formulation:**
  $$r = \frac{\sum (x_i - \bar{x})(\hat{x}_i - \bar{\hat{x}})}{\sqrt{\sum (x_i - \bar{x})^2 \sum (\hat{x}_i - \bar{\hat{x}})^2}}, \quad \text{MSE} = \frac{1}{d}\|\mathbf{x} - \hat{\mathbf{x}}\|_2^2$$
* **Security Claim:** Un-clipped empirical measurement of gradient leakage under SecAgg and DP protection.
* **Expected Invariant:** SecAgg masking reduces Pearson correlation to near zero ($r < 0.10$).
* **Possible Implementation Risks:** Zero variance in inputs causing zero Pearson denominator.
* **Edge Cases:** Single dimension $d=1$, zero vector inputs.
* **Scientific Claim:** Empirical DLG feature reconstruction leakage evaluator.
* **Appropriate Verification Methodology:** Unit contract test & correlation verification.

---

### Component 12: Pipeline Incompatibility Guard (`InvalidPipelineConfigurationError`)
* **Purpose:** Blocks invalid configuration pairings of additive zero-sum SecAgg with non-linear distance/rank Byzantine defenses (Krum, Coordinate-wise Median, Bulyan, Trimmed Mean) at simulation initialization.
* **Mathematical Formulation:**
  $$\text{If } \text{SecAgg}=\text{True} \text{ and } \text{Method} \in \{\text{Krum}, \text{Median}, \text{Bulyan}, \text{TrimmedMean}\} \implies \text{Raise InvalidPipelineConfigurationError}$$
* **Security Claim:** Prevents mathematical pipeline corruption where additive noise masks $\|m_i\|_2 \gg \|w_i\|_2$ distort Euclidean distance metrics ($\|m_i - m_j\|_2$), causing Byzantine defenses to select random noise vectors.
* **Expected Invariant:** Incompatible configurations fail fast at simulation startup; FedAvg/FedOpt pairings execute cleanly.
* **Possible Implementation Risks:** Missing newly added non-linear Byzantine methods in the guard check set.
* **Edge Cases:** Both SecAgg and DP enabled simultaneously with FedAvg.
* **Scientific Claim:** Runtime pipeline compatibility enforcement guard.
* **Appropriate Verification Methodology:** Incompatible pipeline configuration unit test.

---

*This document completes the scientific verification inventory for the Secure Aggregation subsystem.*
