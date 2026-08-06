# Scientific Verification Roadmap: Federated GraphSAGE (FedGNN)

**Auditor Role:** Senior Researcher in Graph Neural Networks, Federated Learning, and Scientific Software Verification  
**Target System:** Federated GraphSAGE (FedGNN) Implementation (`graph_embedding_model.py`, `graph_embedding_service.py`, `fl_engine.py`)  
**Repository Path:** `verification/graph_intelligence/tests/graph_sage_verification_roadmap.md`

---

## 1. Overview & Verification Methodologies

To ensure complete mathematical correctness, numerical precision, privacy preservation, and operational robustness, the GraphSAGE / FedGNN implementation is evaluated across 10 specialized verification methodologies:

1. **Independent Mathematical Reference Implementation:** Compares production outputs against a zero-dependency NumPy reference model to detect PyTorch autograd or indexing errors.
2. **Property-Based Testing (Hypothesis):** Verifies graph-theoretic invariants (e.g. permutation invariance, unit-sphere norms, serialization bijectivity) across hundreds of randomized graph structures.
3. **Numerical Verification:** Validates floating-point precision ($E_{\text{max}} < 10^{-7}$) and gradient stability ($\nabla_{\theta} \mathcal{L} \neq \text{NaN}$).
4. **Unit Testing:** Exercises individual functions under isolated, deterministic graph inputs.
5. **Representation Quality Evaluation:** Assesses intra-class vs. inter-class cosine similarity separation ($\text{Sim}_{\text{intra}} > \text{Sim}_{\text{inter}}$) on unit-sphere embedding spaces.
6. **Embedding Stability Analysis:** Evaluates vector stability under random seed perturbations and graph structural modifications.
7. **Robustness & Adversarial Testing:** Injects missing fields, isolated nodes, NaN/Inf features, duplicate edges, and out-of-bounds neighbor indices to verify graceful degradation.
8. **Statistical Validation:** Measures variance and empirical distribution bounds of DP-noised vectors.
9. **Scalability & Complexity Benchmarking:** Validates empirical time and memory scaling ($O(|\mathcal{E}_{\text{sampled}}| \cdot d)$) against theoretical bounds.
10. **Privacy & Security Evaluation:** Tests Differential Privacy noise calibration, query rate-limiting exhaustion (`max_query_budget`), and classifier head parameter isolation.

---

## 2. Component-by-Component Verification Roadmap

### Component 1: Feature Extraction Pipeline (`extract_node_features`)
* **Verification Target:** 12-dimensional numerical vector construction from heterogeneous entity metadata.
* **Appropriate Methods:** **Unit Testing**, **Property-Based Testing**, **Numerical Verification**.
* **Methodology Justification:** Property-based testing ensures feature outputs remain bounded in $[0.0, 1.0]$ across arbitrary dictionary inputs; unit tests verify deterministic fallback logic for invalid date formats.
* **Pass/Fail Criteria:** $\dim(\mathbf{x}_v) = 12$, $\sum_{i=0}^{6} x_{v,i} = 1.0$, $x_{v, j} \in [0, 1]$ for all $j \in \{0, \dots, 11\}$.

---

### Component 2: PyTorch Sparse COO GraphSAGE Layer (`GraphSAGELayer`)
* **Verification Target:** Inductive 1-hop message passing, sparse matrix mean aggregation, linear projections, ReLU, and L2 unit-sphere normalization.
* **Appropriate Methods:** **Independent Mathematical Reference**, **Property-Based Testing**, **Numerical Verification**.
* **Methodology Justification:** Comparing PyTorch sparse matrix multiplication outputs against pure NumPy matrix multiplication isolates autograd and sparse indexing bugs; Hypothesis property tests verify neighbor order permutation invariance ($\text{AGG}(\pi(\mathcal{N})) \equiv \text{AGG}(\mathcal{N})$).
* **Pass/Fail Criteria:** $E_{\text{max}} = \|\mathbf{H}_{\text{PyTorch}} - \mathbf{H}_{\text{NumPy}}\|_{\infty} < 10^{-6}$; L2 norm $\|\hat{\mathbf{h}}_v\|_2 = 1.000000 \pm 10^{-5}$.

---

### Component 3: Multi-Layer Model Architecture & Classifier Head (`GraphSAGEModel`)
* **Verification Target:** 2-layer GraphSAGE representation extraction and isolated local classifier head ($64 \rightarrow 16 \rightarrow 1$).
* **Appropriate Methods:** **Independent Mathematical Reference**, **Unit Testing**, **Privacy Evaluation**.
* **Methodology Justification:** Unit tests assert that calling `to_model_weights(include_classifier=False)` excludes classifier head parameters, ensuring local label distributions cannot leak during FL parameter exchange.
* **Pass/Fail Criteria:** `num_params(include_classifier=False)` strictly excludes 1,057 classifier parameters; 2-hop forward pass matches NumPy 2-hop calculation to $E_{\text{max}} < 10^{-6}$.

---

### Component 4: In-Memory Subgraph Construction (`build_local_graph`)
* **Verification Target:** Construction of node feature matrix, undirected adjacency lists, and binarized fraud labels (`HIGH`/`CRITICAL` = 1.0).
* **Appropriate Methods:** **Unit Testing**, **Robustness Testing**.
* **Methodology Justification:** Robustness tests inject disconnected components and missing relationships to ensure node index mappings stay contiguous $\{0, 1, \dots, N-1\}$ without key errors.
* **Pass/Fail Criteria:** Adjacency lists are strictly symmetric ($u \in \mathcal{A}[v] \iff v \in \mathcal{A}[u]$); fraud labels $\mathbf{y} \in \{0.0, 1.0\}^N$.

---

### Component 5: Local Subgraph Gradient Optimization (`train_local_gnn`)
* **Verification Target:** Local supervised training via Binary Cross-Entropy Loss ($\mathcal{L}_{\text{BCE}}$) and Adam optimizer.
* **Appropriate Methods:** **Numerical Verification**, **Robustness Testing**, **Embedding Stability Analysis**.
* **Methodology Justification:** Testing local GNN training under 0% fraud and 100% fraud subgraphs verifies numerical stability ($\mathcal{L} \neq \text{NaN}$) under extreme class imbalance.
* **Pass/Fail Criteria:** $\mathcal{L}_{\text{BCE}}$ decreases monotonically over epochs; training on zero-fraud graphs completes without NaN loss or process termination.

---

### Component 6: DP Noise Injection & Export Re-Normalization (`get_all_embeddings`)
* **Verification Target:** Calibrated Gaussian noise addition $\boldsymbol{\eta} \sim \mathcal{N}(0, \sigma^2 \mathbf{I})$ and L2 unit-sphere re-normalization.
* **Appropriate Methods:** **Statistical Validation**, **Privacy Evaluation**, **Numerical Verification**.
* **Methodology Justification:** Statistical evaluation measures sample variance of exported embeddings to verify noise magnitude; numerical checks assert that post-noise re-normalization recovers exact unit norm $\|\hat{\mathbf{h}}^{\text{DP}}\|_2 = 1.0$.
* **Pass/Fail Criteria:** Exported embeddings are perturbed ($\hat{\mathbf{h}}^{\text{DP}} \neq \hat{\mathbf{h}}$) while maintaining $\|\hat{\mathbf{h}}^{\text{DP}}\|_2 = 1.000000 \pm 10^{-5}$.

---

### Component 7: Zero-Knowledge Similarity Search & Rate Limiting (`find_similar_entities`)
* **Verification Target:** Cosine similarity calculation and query rate-limiting budget enforcement (`max_query_budget`).
* **Appropriate Methods:** **Unit Testing**, **Robustness Testing**, **Privacy Evaluation**.
* **Methodology Justification:** Robustness testing executes queries repeatedly until $C_q(u) \ge B_{\text{max}}$ to confirm that the service rejects further requests with `[]`, blocking binary-search membership triangulation.
* **Pass/Fail Criteria:** Cosine similarity scores bounded in $[-1.0, 1.0]$; query count $> B_{\text{max}}$ halts search immediately.

---

### Component 8: Bijective Parameter Serialization (`to_model_weights` / `load_model_weights`)
* **Verification Target:** Flattening PyTorch parameters into `ModelWeights` floats and reconstructing state dicts.
* **Appropriate Methods:** **Property-Based Testing (Hypothesis)**, **Numerical Verification**.
* **Methodology Justification:** Hypothesis property testing sweeps randomized hidden dimensions and layer depths to prove round-trip bijection identity $\text{load}(\text{to}(\mathbf{M})) \equiv \mathbf{M}$.
* **Pass/Fail Criteria:** Maximum absolute serialization error $E_{\text{max}} = 0.00e+00$.

---

### Component 9: Federated GNN Parameter Aggregation (`aggregate_graph_parameters`)
* **Verification Target:** FedAvg weighted parameter averaging and layer shape validation across banks.
* **Appropriate Methods:** **Independent Mathematical Reference**, **Robustness Testing**, **Property-Based Testing**.
* **Methodology Justification:** Independent NumPy reference comparison verifies exact weighted linear combination; robustness tests confirm that mismatched client layer shapes raise a descriptive `ValueError`.
* **Pass/Fail Criteria:** FedAvg output matches NumPy weighted average to $E_{\text{max}} = 0.00e+00$; shape mismatches cleanly rejected.

---

## 3. Verification Execution Matrix

| Subsystem | Target File | Verification Method | Associated Test Script | Status |
|:---|:---|:---|:---|:---:|
| Feature Pipeline | `graph_embedding_model.py` | Property-Based & Unit Testing | `test_graph_sage_hypothesis.py` | 🟢 **PASSED** |
| Sparse Message Passing | `graph_embedding_model.py` | Independent NumPy Reference | `graph_sage_reference_verification.py` | 🟢 **PASSED** |
| Model & Classifier | `graph_embedding_model.py` | Privacy & Size Isolation | `test_graph_sage_robustness.py` | 🟢 **PASSED** |
| Subgraph Construction | `graph_embedding_service.py` | Robustness & Edge Stress | `test_graph_sage_robustness.py` | 🟢 **PASSED** |
| Local Training | `graph_embedding_service.py` | Extreme Imbalance Stress | `test_graph_sage_robustness.py` | 🟢 **PASSED** |
| DP Noise Export | `graph_embedding_service.py` | Statistical & Privacy | `test_graph_sage_robustness.py` | 🟢 **PASSED** |
| Similarity Search | `graph_embedding_service.py` | Rate Limit & Domain Bounds | `test_graph_sage_robustness.py` | 🟢 **PASSED** |
| Serialization | `graph_embedding_model.py` | Hypothesis Bijection | `test_graph_sage_hypothesis.py` | 🟢 **PASSED** |
| FedAvg Aggregation | `fl_engine.py` | Mathematical Reference | `graph_sage_reference_verification.py` | 🟢 **PASSED** |
