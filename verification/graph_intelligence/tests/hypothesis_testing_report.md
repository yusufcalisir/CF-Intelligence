# Federated GraphSAGE (FedGNN): Hypothesis Property-Based Testing Report

**Status: ALL PROPERTIES PASSED (6/6 Invariants Verified across 350 Randomized Scenarios)** ✓  
**Execution Date:** 2026-07-31  
**Framework:** Hypothesis 6.156.5 + PyTest 8.4.2  
**Test File:** `scratch/test_graph_sage_hypothesis.py`  
**Target Class:** `GraphSAGELayer`, `GraphSAGEModel` (`graph_embedding_model.py`), `aggregate_graph_parameters` (`fl_engine.py`)

---

## Executive Summary

Property-based testing systematically evaluates mathematical and graph-theoretic invariants across hundreds of generated parameter states (randomized graph sizes $N \in [2, 40]$, feature dimensions $D \in [4, 32]$, dense/sparse/disconnected adjacencies, shuffled neighbor orderings, parameter shapes, and federated client partitions).

A total of **350 randomized scenarios** were evaluated across 6 distinct property specifications. All 6 properties passed cleanly without a single invariant violation or floating-point exception.

---

## Detailed Invariant & Property Results

### Property 1: Neighborhood Permutation Invariance
- **Mathematical Formulation:**
  $$\forall v \in V, \quad \forall \text{permutation } \pi, \quad \text{AGG}(\pi(\mathcal{N}(v))) \equiv \text{AGG}(\mathcal{N}(v)) \implies \|\text{Emb}(G) - \text{Emb}(G_{\text{shuffled}})\|_\infty = 0.0$$
- **Hypothesis Configuration:**
  - Random graph node counts $N \in [2, 40]$, input dimensions $D \in [4, 32]$
  - Random sparse/dense adjacency lists $\mathcal{N}(v)$
  - Neighbor order array $\mathcal{N}(v)$ shuffled randomly using NumPy permutations for every node.
  - 50 randomized test cases
- **Verification Criteria:**
  - $\max | \mathbf{h}_{\text{orig}} - \mathbf{h}_{\text{shuffled}} | < 10^{-5}$
- **Result:** **PASSED** (50/50 runs) ✓

---

### Property 2: L2 Hypersphere Unit Norm Invariant
- **Mathematical Formulation:**
  $$\forall v \in V, \quad \mathbf{h}_v = \frac{\mathbf{z}_v}{\|\mathbf{z}_v\|_2} \implies \|\mathbf{h}_v\|_2 = 1.0 \pm 10^{-6} \quad (\text{if } \mathbf{z}_v \ne \mathbf{0})$$
- **Hypothesis Configuration:**
  - Multi-layer GraphSAGE models with embedding dimensions $D_{\text{emb}} \in \{16, 32, 64\}$
  - Random input feature matrices $\mathbf{X} \in \mathbb{R}^{N \times D_{\text{in}}}$
  - 50 randomized test cases
- **Verification Criteria:**
  - $\forall v, \quad |\|\mathbf{h}_v\|_2 - 1.0| < 10^{-5}$ for all non-zero activation vectors.
- **Result:** **PASSED** (50/50 runs) ✓

---

### Property 3: Cosine Similarity Domain Boundedness & Symmetry
- **Mathematical Formulation:**
  $$\forall \mathbf{u}, \mathbf{v} \in \mathbb{R}^d, \quad -1.0 \le \text{Sim}(\mathbf{u}, \mathbf{v}) \le 1.0 \quad \text{and} \quad \text{Sim}(\mathbf{u}, \mathbf{v}) \equiv \text{Sim}(\mathbf{v}, \mathbf{u})$$
- **Hypothesis Configuration:**
  - Random embedding vectors $\mathbf{u}, \mathbf{v}$ generated across $d \in \{16, 32, 64\}$
  - 100 randomized test cases
- **Verification Criteria:**
  - $-1.0 - 10^{-6} \le \text{Sim} \le 1.0 + 10^{-6}$ and $|\text{Sim}(\mathbf{u}, \mathbf{v}) - \text{Sim}(\mathbf{v}, \mathbf{u})| < 10^{-6}$
- **Result:** **PASSED** (100/100 runs) ✓

---

### Property 4: Model Weight Serialization Round-Trip Bijection
- **Mathematical Formulation:**
  $$\forall \mathbf{M} \in \text{GraphSAGEModel}, \quad \text{load\_model\_weights}(\text{to\_model\_weights}(\mathbf{M})) \equiv \mathbf{M}$$
- **Hypothesis Configuration:**
  - GNN architectures generated with varying input dims $[4, 32]$, hidden dims $\{16, 32, 64\}$, embedding dims $\{8, 16, 32\}$, and layer depth $[1, 3]$
  - 50 randomized test cases
- **Verification Criteria:**
  - Parameter tensor difference between original and restored model equals exactly $0.00\text{e}+00$.
- **Result:** **PASSED** (50/50 runs) ✓

---

### Property 5: Federated GNN Aggregation Scale Invariance & Convexity
- **Mathematical Formulation:**
  $$\forall \boldsymbol{\theta}_1 = \dots = \boldsymbol{\theta}_K, \quad \sum_{k=1}^K \frac{n_k}{\sum n_j} \boldsymbol{\theta}_k \equiv \boldsymbol{\theta}_1$$
- **Hypothesis Configuration:**
  - Client counts $K \in [2, 10]$, sample partitions $n_k \in [1, 1000]$
  - 50 randomized test cases
- **Verification Criteria:**
  - $\|\boldsymbol{\theta}_{\text{aggregated}} - \boldsymbol{\theta}_1\|_\infty < 10^{-6}$
- **Result:** **PASSED** (50/50 runs) ✓

---

### Property 6: Isolated Node Isolation Safety & Self-Loop Fallback
- **Mathematical Formulation:**
  $$\forall v \text{ with } \mathcal{N}(v) = \emptyset, \quad \text{AGG}(\emptyset) = \mathbf{x}_v \implies \mathbf{h}_v \text{ is finite, non-NaN, unit norm}.$$
- **Hypothesis Configuration:**
  - Completely disconnected graphs ($E = \emptyset$) with isolated nodes
  - 50 randomized test cases
- **Verification Criteria:**
  - Forward pass executes cleanly without indexing out-of-bounds, returning valid unit-norm embeddings.
- **Result:** **PASSED** (50/50 runs) ✓

---

## Test Summary Matrix

| Property ID | Invariant Verified | Scenarios | Result | Key Observation |
|:---|:---|:---:|:---:|:---|
| **P1** | Neighborhood Permutation Invariance | 50 | **PASSED** ✓ | Shuffled neighbor ordering invariant |
| **P2** | L2 Hypersphere Unit Norm ($\|\mathbf{h}\|_2 = 1$) | 50 | **PASSED** ✓ | Unit hypersphere projection verified |
| **P3** | Cosine Similarity Bounds & Symmetry | 100 | **PASSED** ✓ | Exact symmetry & domain bounds |
| **P4** | Serialization Bijection | 50 | **PASSED** ✓ | Lossless parameter round-trip |
| **P5** | FedAvg Convexity Invariant | 50 | **PASSED** ✓ | Parameter scale invariance confirmed |
| **P6** | Isolated Node Self-Loop Fallback | 50 | **PASSED** ✓ | Zero-crash disconnected graph safety |
